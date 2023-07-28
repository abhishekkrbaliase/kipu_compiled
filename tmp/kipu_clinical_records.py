"""
pip3 install -U selenium -y
pip3 install pandas -y
pip3 install webdriver-manager -y
"""
import csv
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

import pandas
import pytz
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from chromedriver_py import binary_path
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger('kipu_clinical_check')
url_mappings = {
    "CC": "https://chapterscapistrano.kipuworks.com",
    "WSR": "https://willowspringsrecovery.kipuworks.com",
    "MSR": "https://sunshinebehavioral.kipuworks.com",
    "MS": "https://monarchshores.kipuworks.com",
    "LR": "https://sunshinebehavioral.kipuworks.com"
}

pacific_time_zone = pytz.timezone('US/Pacific')
utc_time_zone = pytz.timezone('UTC')


def get_salesforce_token(consumer_key, consumer_secret,
                         base_url="https://sunshinebhcllc--auditdev.sandbox.my.salesforce.com"):
    params = {'grant_type': 'client_credentials', 'client_id': consumer_key, 'client_secret': consumer_secret}
    response = requests.request("GET", base_url + '/services/oauth2/token', params=params)
    if response.status_code is 200:
        values = response.json()
        response.close()
        return values['access_token']
    return ""


def get_new_salesforce_job_id(auth_token, base_url="https://sunshinebhcllc--auditdev.sandbox.my.salesforce.com"):
    headers = {
        'Authorization': 'Bearer {0}'.format(auth_token),
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    line_ending = "LF"
    if os.name == 'nt':
        line_ending = "CRLF"
    payload = {
        "object": "KIPU_Audit_Clinical_Progress_Note__c",
        "externalIdFieldName": "KIPU_Form_ID__c",
        "contentType": "CSV",
        "operation": "upsert",
        "lineEnding": "{}".format(line_ending)
    }
    response = requests.request("POST", base_url + '/services/data/v57.0/jobs/ingest/', headers=headers,
                                data=json.dumps(payload))
    if response.status_code is 200:
        values = response.json()
        response.close()
        return values['id']
    return ""


def push_to_salesforce(temp_file_csv_file, auth_token,
                       base_url="https://sunshinebhcllc--auditdev.sandbox.my.salesforce.com",
                       job_id=None):
    if not job_id:
        job_id = get_new_salesforce_job_id(auth_token, base_url)
    if job_id == '':
        return 'missingAuth'
    insert_service_path = "/services/data/v57.0/jobs/ingest/{0}/batches".format(job_id)
    close_job_path = job_status_path = "/services/data/v57.0/jobs/ingest/{0}/".format(job_id)
    close_job_body = '{ "state" : "UploadComplete" }'

    # Insert
    headers = {
        'Authorization': 'Bearer {0}'.format(auth_token),
        'Content-Type': 'text/csv',
        'Accept': 'application/json'
    }

    with open(temp_file_csv_file.name, 'rb') as payload:
        response = requests.request("PUT", base_url + insert_service_path, headers=headers, data=payload)
        response.close()

    headers['Content-Type'] = "application/json"
    response = requests.request("PATCH", base_url + close_job_path, headers=headers, data=close_job_body)
    response.close()

    job_completed = False
    while not job_completed:
        time.sleep(3)
        print("Current job status is: ")
        if 'Content-Type' in headers:
            headers.pop('Content-Type')
        response = requests.request("GET", headers=headers, url=base_url + job_status_path)
        value = response.json()
        response.close()
        print(value['state'])
        logger.debug("Salesforce job id is {}", str(job_id))
        if value['state'] == 'JobComplete' and int(value["numberRecordsFailed"]) == 0:
            print("Inserted {} records to Salesforce with JobID {} \n".format(value['numberRecordsProcessed'], job_id))
            return ''
        if value['state'] == 'Failed' or int(value["numberRecordsFailed"]) > 0:
            print("Insert to Salesforce job {} Failed".format(job_id))
            return 'Failed'


def write_records(csv_file, consumer_key, consumer_secret,
                  base_url="https://sunshinebhcllc--auditdev.sandbox.my.salesforce.com"):
    processed = False
    while not processed:
        auth_token = get_salesforce_token(consumer_key, consumer_secret, base_url)
        ret_st = push_to_salesforce(csv_file, auth_token, base_url)
        if ret_st == 'missingAuth':
            continue
        else:
            print("Processing Done ====> \n")
            print(ret_st)
            processed = True


def search_mrn(browser_ptr, patient_mrn_entered, kipu_base_url):
    browser_ptr.get(kipu_base_url + "/patients")
    patient_id = None
    search_box = browser_ptr.find_element(By.ID, "term_form")
    search_box.send_keys(patient_mrn_entered)
    search_box.send_keys(Keys.RETURN)
    is_next_present = True

    while is_next_present:
        search_table_data = browser_ptr.find_element(By.ID, "patient_search_result")
        all_tr = search_table_data.find_elements(By.TAG_NAME, "tr")
        for tr in all_tr:
            if patient_mrn_entered in tr.text.split():
                patient_search_href = tr.find_element(By.TAG_NAME, "a")
                if patient_search_href.get_attribute("href") and "/patients/" in patient_search_href.get_attribute(
                        "href"):
                    patient_id = patient_search_href.get_attribute("href").split("/patients/", 1)[1]
                    browser_ptr.get(kipu_base_url + "/patients/" + patient_id)
                    is_next_present = False  # Don't loop again
                    break
        if not patient_id:
            next_button = browser_ptr.find_element(By.ID, "patient_search_result_paginate") \
                .find_element("xpath", "//*[contains(text(),'Next')]")
            is_next_present = 'disabled' not in next_button.get_attribute('class')
            if is_next_present:
                element = WebDriverWait(browser_ptr, 60).until(
                    EC.element_to_be_clickable((By.XPATH, "//*[contains(text(),'Next')]")))
                browser_ptr.execute_script("window.scrollTo(0, document.body.scrollHeight);")  # Scroll Down
                try:
                    element.click()
                except Exception:
                    element.click()  # As it might not be clickable earlier, click again

    if not patient_id:
        print("MRN not found {0}".format(patient_mrn_entered))
        return None
    return patient_id


def get_24_hour_time(s):
    s = "".join(s.split())
    hour = int(s[:2]) % 12
    minutes = int(s[3:5])
    if s[5] == 'P' or s[5] == 'p':
        hour += 12
    return "{:02}:{:02}".format(hour, minutes)


def format_date_time(date_time_str, return_format='%Y-%m-%dT%H:%M:%S.000Z'):
    """Takes str in format '04/13/2023 10:26 PM' returns YYYY-MM-DD"""
    pt_time = pacific_time_zone.localize(datetime.strptime(date_time_str, '%m/%d/%Y %I:%M %p'))
    utc_time = pt_time.astimezone(utc_time_zone)
    return utc_time.strftime(return_format)


def remove_time(date_time_str, return_format='%Y-%m-%dT%H:%M:%S.000Z'):
    """Takes str in format '04/13/2023 10:26 PM' returns YYYY-MM-DD"""
    return datetime.strptime(date_time_str, '%m/%d/%Y %I:%M %p').date().strftime(return_format)


def fill_form_data(browser, ret_data):
    for cur_row in ret_data:
        if 'link' in cur_row:
            browser.get(cur_row['link'])
            kipu_form_id__c = browser.current_url.split("/patient_evaluations/")[1].split('?')[0]
            cur_row['KIPU_Form_ID__c'] = kipu_form_id__c

            # get Start and End time
            root_element = browser.find_element(By.ID, "show_patient_evaluation")
            start_time = root_element \
                .find_element("xpath", "//*[contains(text(),'Start time')]") \
                .find_element("xpath", "./..").text.split("\n")[1]
            end_time = root_element \
                .find_element("xpath", "//*[contains(text(),'End time')]") \
                .find_element("xpath", "./..").text.split("\n")[1]
            cur_row['Start_Date_Time__c'] = format_date_time(start_time)
            cur_row['End_Date_Time__c'] = format_date_time(end_time)

            # get Attended
            if len(root_element.find_elements("xpath",
                                              "//*[contains(text(),'ATTENDANCE')]")) > 0 \
                    and len(
                root_element.find_element("xpath", "//*[contains(text(),'ATTENDANCE')]").find_elements(
                    "xpath", "//*[contains(text(),'Attended')]")) > 0:
                cur_row['Attended__c'] = 'Yes'
            else:
                cur_row['Attended__c'] = 'No'
            if 'bps' in cur_row['form_name'] or 'biopsychosocial assessment' in cur_row['form_name']:
                cur_row['Attended__c'] = 'Yes'

            # get sign and review data
            sign_row = browser.find_elements(By.CLASS_NAME, 'border_top')
            sign_row_split = sign_row[0].text.split(",")
            cur_row['Signed_By__c'] = sign_row_split[0]
            cur_row['Date_Signed__c'] = remove_time(
                sign_row_split[len(sign_row_split) - 1].strip(), '%Y-%m-%d'  # Abhishek Check Time as well for PT to GMT
            )
            if len(sign_row) > 1:
                review_row_split = sign_row[1].text.split(",")  # Abhishek - Check Review name coming wrong
                cur_row['Review_By__c'] = review_row_split[0]
                cur_row['Date_Reviewed__c'] = remove_time(
                    review_row_split[len(review_row_split) - 1].strip(), '%Y-%m-%d'
                )

            # get type of progress note
            if 'family progress note' in cur_row['form_name']:
                cur_row['Type_of_Progress_Note__c'] = 'Therapist Progress Note'
            elif cur_row['centre'] == 'LR' or cur_row['centre'] == 'WSR':
                cur_row['Type_of_Progress_Note__c'] = 'Therapist Progress Note'
            elif cur_row['form_name'] == 'bps' or 'biopsychosocial assessment' in cur_row['form_name']:
                cur_row['Type_of_Progress_Note__c'] = 'Case Manager Progress Note'
            elif 'case manager progress note' in cur_row['form_name']:
                cur_row['Type_of_Progress_Note__c'] = 'Case Manager Progress Note'
            else:
                cur_row['Type_of_Progress_Note__c'] = 'Therapist Progress Note'


def get_tab_level_data(driver, href, mr_number, processing_date, processing_centre, kipu_chart_audit__c):
    driver.get(href)
    # Check for Status of processing_date
    rows = driver.find_elements("xpath", "//*[@class='grid_index']/tbody/tr")
    tab_level_data = []
    form_name_list = [
        'family progress note',
        'therapist progress note',
        'case manager progress note',
        'bps',
        'biopsychosocial assessment',
        'clinical progress note'
    ]
    for i in range(len(rows)):
        if i > 0 and rows[i].text:
            status = None
            date = None
            cur_row = {'mrn': mr_number, 'key': 'value', 'KIPU_Chart_Audit__c': kipu_chart_audit__c}
            form_name = rows[i].find_elements(By.TAG_NAME, 'td')[0].text.lower()
            match_list = list(filter(lambda x: x in form_name, form_name_list))
            if not match_list:
                continue
            if rows[i].find_elements(By.TAG_NAME, 'td'):
                status_date_column = rows[i].find_elements(By.TAG_NAME, 'td')[1].text  # second column's text
                status = status_date_column.split("\n")[0]
                if len(status_date_column.split("\n")) > 1:
                    date = status_date_column.split("\n")[1]
            if not status:
                continue
            if 'open' in status.lower() \
                    or 'inprogress' in status.lower().replace(" ",
                                                              ""):  # date may not be present for open or in progress
                cur_row['status'] = status
                cur_row['form_name'] = form_name  # Set Original form name
                tab_level_data.append(cur_row)
            elif date and processing_date == date:
                # check status. If not 'open' or 'inprogress' click and extract data else just log
                link = rows[i].find_element(By.TAG_NAME, "a").get_attribute('href')
                form_name = match_list[0]
                cur_row['form_name'] = form_name  # Set custom name from list
                cur_row['status'] = status
                cur_row['link'] = link
                cur_row['centre'] = processing_centre
                tab_level_data.append(cur_row)
    return tab_level_data


def get_mrn_data(browser_ptr, mr_number, processing_date, base_url, processing_centre, salesforce_id):
    patient_id = search_mrn(browser_ptr, mr_number, base_url)
    if not patient_id:
        return None
    print("Found {}. Processing it!".format(mr_number))
    # Process Clinical and Progress Tab
    clinical_tab_href = browser_ptr.find_element(By.LINK_TEXT, "Clinical").get_attribute("href")
    progress_notes_href = browser_ptr.find_element(By.LINK_TEXT, "Progress Notes").get_attribute("href")

    clinic_data = get_tab_level_data(
        driver=browser_ptr,
        href=clinical_tab_href,
        mr_number=mr_number,
        processing_date=processing_date,
        processing_centre=processing_centre,
        kipu_chart_audit__c=salesforce_id
    )

    progress_data = get_tab_level_data(
        driver=browser_ptr,
        href=progress_notes_href,
        mr_number=mr_number,
        processing_date=processing_date,
        processing_centre=processing_centre,
        kipu_chart_audit__c=salesforce_id
    )

    fill_form_data(browser_ptr, clinic_data)
    fill_form_data(browser_ptr, progress_data)

    clinic_data.extend(progress_data)

    return clinic_data


def login(browser, passed_configs):
    centre_selected = False
    selected_centre = ''
    while centre_selected is False:
        print("Enter Sunshine Centre Code - CC / WSR / MSR / MS / LR")
        selected_centre = input()
        try:
            selected_centre = selected_centre.strip()
            url_mapped = url_mappings[selected_centre]
            centre_selected = True
        except KeyError:
            print("Invalid Centre Entered. Accepted list is CC / WSR / MSR / MS / LR")

    try:
        email_id = passed_configs[selected_centre.strip() + '_Username'].strip()
        password = passed_configs[selected_centre.strip() + '_Password'].strip()
    except KeyError as e:
        print("{} not provided. Shutting Down. Please configure the file and start the application".format(e.args[0]))
        browser.close()
        sys.exit(1)

    browser.get(url_mapped + "/users/sign_in")

    if 'sign_in' in browser.current_url:
        email = browser.find_element(By.ID, "user_login")
        email.send_keys(email_id)  # "deepakk@sunshinebh.com"

        password_box = browser.find_element(By.ID, "user_password")
        password_box.send_keys(password)
        password_box.send_keys(Keys.RETURN)

    if 'sign_in' in browser.current_url:
        logger.debug(""" User still didn't get signed-in after entering credentials8x8""")
        print('Sign-In failed. Check Credentials')
        logger.error('Sign-In failed. Check Credentials')
        sys.exit(1)

    if "password_expired" in browser.current_url:
        print("Password expired. Please reset it and configure in the file")
        logger.info("Password expired. Please reset it and configure in the file")
        sys.exit(1)

    print("Checking for Two Factor Authentication")
    is_two_factor_page = "two_factor_authentication" in browser.current_url

    while is_two_factor_page:
        hrefs = browser.find_elements(By.TAG_NAME, "a")
        for href in hrefs:
            if href.get_attribute("href") and "send_code" in href.get_attribute("href"):
                href.click()
                break
        print("Enter OTP")
        otp = input()
        otp_box = browser.find_element(By.ID, "code")
        otp_box.send_keys(otp)
        otp_box.send_keys(Keys.RETURN)
        is_two_factor_page = "two_factor_authentication" in browser.current_url

    print("Logged in to Kipu..")

    if selected_centre == 'LR':  # Default dropdown is LR (Lincoln Recovery)
        browser.maximize_window()
        browser.find_element(By.ID, 'switch_my_location').click()
        time.sleep(2)
        all_dropdown = browser.find_elements(By.TAG_NAME, 'li')
        for dd in all_dropdown:
            if 'Lincoln Recovery' in dd.text:
                dd.click()
                break
    elif selected_centre == 'MSR':  # Default dropdown is LR (Lincoln Recovery)
        browser.maximize_window()
        browser.find_element(By.ID, 'switch_my_location').click()
        time.sleep(2)
        all_dropdown = browser.find_elements(By.TAG_NAME, 'li')
        for dd in all_dropdown:
            if 'Mountain Springs Recovery' in dd.text:
                dd.click()
                break
    return url_mapped, selected_centre


def prepare_df_for_salesforce(temp_df, list_columns):
    if 'link' in temp_df:
        open_status = temp_df[temp_df['link'].isna()]
    else:
        return temp_df, None
    salesforce_df = temp_df[temp_df['link'].notna()]
    salesforce_df = salesforce_df[salesforce_df.columns.intersection(list_columns)]
    return open_status, salesforce_df


def main():
    chrome_options = Options()
    chrome_options.headless = True
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_experimental_option("excludeSwitches", ['enable-logging'])
    service_object = Service(binary_path)
    browser = webdriver.Chrome(service=service_object,
                               options=chrome_options, service_log_path=os.devnull)
    logging_level_set = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO
    }

    file_read = sys.argv[1]
    print("Configuration File Path picked as {}".format(file_read))
    configs = {}
    with open(file_read, "r") as f:
        for line in f.readlines():
            line_str = line.split(':', 1)  # split on first occurrence only
            if len(line_str) != 2:
                continue
            configs[str(line_str[0]).strip()] = str(line_str[1]).strip()

    if len(sys.argv) == 3:
        logger.setLevel(logging_level_set[sys.argv[2]])
    else:
        logger.setLevel(logging_level_set["INFO"])
    file_handler = RotatingFileHandler(os.path.dirname(sys.argv[1]) + os.path.sep + "logs.log",
                                       maxBytes=1024 * 1024 * 10,
                                       backupCount=3)  # abhishek - remove console logging
    logging.basicConfig(
        format='%(asctime)s %(levelname)-8s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
    logger.addHandler(file_handler)

    # Main Process Starts here
    selected_confs = login(browser, configs)
    url_mapped = selected_confs[0]
    centre = selected_confs[1]
    salesforce_consumer_key = configs[
        'Salesforce_Consumer_Key'].strip()
    salesforce_consumer_secret = configs[
        'Salesforce_Consumer_Secret'].strip()
    salesforce_base_url = configs['Salesforce_URL'].strip()
    # Start Processing data
    print("Enter csv file path")
    csv_filename = input()
    print("Enter date in mm/dd/yyyy e.g. 05/31/2023")
    entered_date = input()
    #   Actual Processing by reading each row in CSV
    mr_patient_rows = []
    failed_MRN = []
    with open(csv_filename, "rt", encoding='ascii') as infile:
        read = csv.DictReader(infile)
        for row in read:
            try:
                start_time = time.time()
                mrn_row_data = get_mrn_data(browser, row['mrn'], entered_date, url_mapped, centre,
                                            row['salesforce_record_id'])
                print("--- For %s took %s seconds for processing ---" % (row['mrn'], time.time() - start_time))
                if mrn_row_data:
                    mr_patient_rows.extend(mrn_row_data)
            except Exception as e:
                print('Exception occurred\n')
                print(e)
                failed_MRN.append(row['mrn'])
    # write data to temporary csv file
    print("Got data from Kipu....")
    _tempFile = tempfile.NamedTemporaryFile(delete=False)
    df = pandas.DataFrame.from_dict(mr_patient_rows)
    print(*mr_patient_rows, sep='\n')
    open_status_df, salesforce_df = prepare_df_for_salesforce(
        df, list_columns=[
            'Attended__c',
            'Date_Reviewed__c',
            'Date_Signed__c',
            'End_Date_Time__c',
            'KIPU_Chart_Audit__c',
            'Review_By__c',
            'Signed_By__c',
            'Start_Date_Time__c',
            'Type_of_Progress_Note__c',
            'KIPU_Form_ID__c'
        ]
    )
    del df
    print("Total 'In Progress' or 'Open' Status is {}".format(open_status_df.shape[0]))
    print("Total failed to get data {}\n{}\n".format(len(failed_MRN), failed_MRN))
    if salesforce_df is None:
        print("Total rowcount to insert in Salesforce is 0")
        browser.quit()
        sys.exit(0)
    print("Total rowcount to insert in Salesforce is {}".format(salesforce_df.shape[0]))
    print("Insert to Salesforce [y/n]")
    is_insert = input().casefold()  # lowercase
    if is_insert != 'y' and is_insert != 'yes':
        sys.exit(0)
    salesforce_df.to_csv(_tempFile, line_terminator=os.linesep, index=False)
    write_records(_tempFile, salesforce_consumer_key, salesforce_consumer_secret, salesforce_base_url)
    _tempFile.close()

    os.remove(_tempFile.name)
    print("\n=========" + " Processed MRNs =========\n")
    browser.quit()


main()

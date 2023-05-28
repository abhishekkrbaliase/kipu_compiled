import csv
import sys
from datetime import datetime
from time import time

import pandas
import pytz
from selenium.webdriver.common.by import By

from kipuUtils.BrowserUtility import BrowserUtility
from kipuUtils.KipuUtility import KipuUtility
from kipuUtils.SalesForceUpsertUtility import SalesForceUpsertUtility


def format_date_time(date_time_str, return_format='%Y-%m-%dT%H:%M:%S.000Z'):
    """Takes str in format '04/13/2023 10:26 PM' returns YYYY-MM-DD"""
    pacific_time_zone = pytz.timezone('US/Pacific')
    utc_time_zone = pytz.timezone('UTC')
    pt_time = pacific_time_zone.localize(datetime.strptime(date_time_str, '%m/%d/%Y %I:%M %p'))
    utc_time = pt_time.astimezone(utc_time_zone)
    return utc_time.strftime(return_format)


def remove_time(date_time_str, return_format='%Y-%m-%dT%H:%M:%S.000Z'):
    """Takes str in format '04/13/2023 10:26 PM' returns YYYY-MM-DD"""
    return datetime.strptime(date_time_str, '%m/%d/%Y %I:%M %p').date().strftime(return_format)


def to_date_no_timezone(date_str, format='%m/%d/%Y', return_format='%Y-%m-%dT%H:%M:%S.000Z'):
    return datetime.strptime(date_str, format).date().strftime(return_format)


def get_tab_level_data(driver, href, mr_number, processing_date, processing_centre, kipu_chart_audit__c):
    driver.get(href)
    # Check for Status of processing_date
    rows = driver.find_elements("xpath", "//*[@class='grid_index']/tbody/tr")
    tab_data = []
    form_name_list = [
        'attachment detox followup',
        'attachment h&p evaluation',
        'attachment h&p',
        'attachment history and physical',
        'attachment attempted followup',
        'attachment psychiatric evaluation',
        'attachment psychiatric followup',
        'h&p evaluation',
        'h&p',
        'medical notes',
        'psychiatric evaluation',
        'psychiatric followup'
    ]
    for i in range(len(rows)):
        if i > 0 and rows[i].text:
            status = None
            date = None
            cur_row = {'mrn': mr_number, 'key': 'value', 'KIPU_Chart_Audit__c': kipu_chart_audit__c}
            form_name = rows[i].find_elements(By.TAG_NAME, 'td')[0].text
            match_list = list(
                filter(lambda x: x.replace(' ', '') in form_name.lower().replace(' ', ''), form_name_list))
            if not match_list:
                continue
            if rows[i].find_elements(By.TAG_NAME, 'td'):
                status_date_column = rows[i].find_elements(By.TAG_NAME, 'td')[1].text  # second column's text
                status = status_date_column.split("\n")[0]
                if len(status_date_column.split("\n")) > 1:
                    date = status_date_column.split("\n")[1]
            if not status:
                continue

            cur_row['Document_Name__c'] = form_name  # Set Original form name

            if 'open' in status.lower() \
                    or 'inprogress' in status.lower().replace(" ",
                                                              ""):  # date may not be present for open or in progress
                cur_row['status'] = status

                tab_data.append(cur_row)
            elif date and processing_date == date:
                # check status. If not 'open' or 'inprogress' click and extract data else just log
                link = rows[i].find_element(By.TAG_NAME, "a").get_attribute('href')
                cur_row['status'] = status
                cur_row['link'] = link
                cur_row['centre'] = processing_centre
                tab_data.append(cur_row)
    return tab_data


def fill_form_data(browser, medical_data):
    for cur_row in medical_data:
        if 'link' in cur_row:
            browser.get(cur_row['link'])
            kipu_form_id__c = browser.current_url.split("/patient_evaluations/")[1].split('?')[0]
            cur_row['KIPU_Form_ID__c'] = kipu_form_id__c
            # Date of Visit from form name
            form_name = cur_row['Document_Name__c']
            form_name_split = form_name.strip().split(' ')
            if ' AM' in form_name or ' PM' in form_name:
                cur_row['Date_of_Visit__c'] = to_date_no_timezone(form_name_split[
                    len(form_name_split) - 3], format='%m/%d/%Y', return_format='%Y-%m-%d')
            else:
                cur_row['Date_of_Visit__c'] = to_date_no_timezone(form_name_split[len(form_name_split) - 1],
                                                                  format='%m/%d/%Y',
                                                                  return_format='%Y-%m-%d')
            browser.get(cur_row['link'])
            # get sign
            sign_row = browser.find_elements(By.CLASS_NAME, 'border_top')
            sign_row_split = sign_row[0].text.split(",")
            cur_row['Signed_Off_By__c'] = sign_row_split[0]
            cur_row['Signed_Off_Date__c'] = remove_time(
                sign_row_split[len(sign_row_split) - 1].strip(), '%Y-%m-%d'
            )


def get_mrn_data(patient_id, browser_ptr, mrn_number, processing_date, base_url, selected_centre, salesforce_id):
    if not patient_id:
        return None
    print('Processing {}'.format(mrn_number))
    # Process Clinical and Progress Tab
    clinical_tab_href = browser_ptr.find_element(By.LINK_TEXT, "Medical").get_attribute("href")
    medical_data = get_tab_level_data(
        driver=browser_ptr,
        href=clinical_tab_href,
        mr_number=mrn_number,
        processing_date=processing_date,
        processing_centre=selected_centre,
        kipu_chart_audit__c=salesforce_id
    )

    fill_form_data(browser_ptr, medical_data)
    return medical_data


def prepare_df_for_salesforce(temp_df, list_columns):
    open_status = temp_df[temp_df['link'].isna()]
    salesforce_df = temp_df[temp_df['link'].notna()]
    salesforce_df = salesforce_df[salesforce_df.columns.intersection(list_columns)]
    return open_status, salesforce_df


def main():
    kipu_util = KipuUtility(sys.argv[1])
    configs = kipu_util.get_config()
    browser = BrowserUtility.get_browser()
    base_url, selected_centre = kipu_util.login(browser)

    print("Enter csv file path")
    csv_filename = input()
    print("Enter date in mm/dd/yyyy e.g. 05/31/2023")
    entered_date = input()
    #   Actual Processing by reading each row in CSV
    mr_patient_rows = []
    failed_mrn = []
    with open(csv_filename, "rt", encoding='ascii') as infile:
        read = csv.DictReader(infile)
        for row in read:
            try:
                start_time = time()
                patient_id = kipu_util.search_mrn(browser, row['mrn'], base_url)
                mrn_row_data = get_mrn_data(patient_id, browser, row['mrn'], entered_date, base_url, selected_centre,
                                            row['salesforce_record_id'])
                print("--- For %s took %s seconds for processing ---" % (row['mrn'], time() - start_time))
                if mrn_row_data:
                    mr_patient_rows.extend(mrn_row_data)
            except Exception as e:
                print(e)
                failed_mrn.append(row['mrn'])
    if not mr_patient_rows:
        print("No Patient record found for date {} to be inserted".format(entered_date))
        browser.quit()
        sys.exit(1)

    # write data to temporary csv file
    print("Got data from Kipu....")

    df = pandas.DataFrame.from_dict(mr_patient_rows)
    print(*mr_patient_rows, sep='\n')
    open_status_df, salesforce_df = prepare_df_for_salesforce(
        df, list_columns=[
            'Date_of_Visit__c',
            'Signed_Off_Date__c',
            'KIPU_Chart_Audit__c',
            'Signed_Off_By__c',
            'Document_Name__c',
            'KIPU_Form_ID__c'
        ]
    )
    del df
    print("Total 'In Progress' or 'Open' Status is {}".format(open_status_df.shape[0]))
    print("Total failed to get data {}\n{}\n".format(len(failed_mrn), failed_mrn))
    print("Total rowcount to insert in Salesforce is {}".format(salesforce_df.shape[0]))
    print("Insert to Salesforce [y/n]")
    is_insert = input().casefold()  # lowercase
    if is_insert != 'y' and is_insert != 'yes':
        browser.quit()
        sys.exit(0)
    sf = SalesForceUpsertUtility('KIPU_Audit_Medical_Progress_Note__c', 'KIPU_Form_ID__c')
    is_written = sf.write_records_using_conf(salesforce_df, configs)
    browser.quit()


main()

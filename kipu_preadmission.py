import csv
import os
import sys
import traceback
from datetime import datetime
from time import time

import pandas
import pytz
from selenium.webdriver.common.by import By

from kipuUtils.BrowserUtility import BrowserUtility
from kipuUtils.KipuUtility import KipuUtility
from kipuUtils.SalesForceUpsertUtility import SalesForceUpsertUtility


def to_date_no_timezone(date_str, format='%m/%d/%Y', return_format='%Y-%m-%dT%H:%M:%S.000Z'):
    return datetime.strptime(date_str, format).date().strftime(return_format)


pacific_time_zone = pytz.timezone('US/Pacific')
utc_time_zone = pytz.timezone('UTC')


def format_date_time(date_time_str, return_format='%Y-%m-%dT%H:%M:%S.000Z', source_format='%m/%d/%Y %I:%M %p'):
    """Takes str in format '04/13/2023 10:26 PM' returns YYYY-MM-DD"""
    pt_time = pacific_time_zone.localize(datetime.strptime(date_time_str, source_format))
    utc_time = pt_time.astimezone(utc_time_zone)
    return utc_time.strftime(return_format)


def get_forms_from_tab(driver, href, form_name_list,
                       processing_date=None):
    driver.get(href)
    # Check for Status of processing_date
    rows = driver.find_elements("xpath", "//*[@class='grid_index']/tbody/tr")
    tab_data = []
    for i in range(len(rows)):
        if i > 0 and rows[i].text:
            status = None
            date = None
            cur_row = {}
            form_name = rows[i].find_elements(By.TAG_NAME, 'td')[0].text
            match_list = list(
                filter(lambda x: x.replace(' ', '') in form_name.lower().replace(' ', ''), form_name_list))
            if not match_list:
                continue
            if rows[i].find_elements(By.TAG_NAME, 'td'):
                status_date_column = rows[i].find_elements(By.TAG_NAME, 'td')[1].text  # second column's text
                status = status_date_column.split('\n')[0]
                if len(status_date_column.split('\n')) > 1:
                    date = status_date_column.split('\n')[1]
            if not status:
                continue
            cur_row['Document_Name__c'] = form_name.lower()  # Set Original form name
            cur_row['status'] = status.lower()
            cur_row['date'] = date
            link = rows[i].find_element(By.TAG_NAME, "a").get_attribute('href')
            cur_row['link'] = link
            tab_data.append(cur_row)
    return tab_data


def form_present(tab_data, forms_list):
    for row in tab_data:
        match_list = list(
            filter(lambda x: x.replace(' ', '') in row['Document_Name__c'].lower().replace(' ', ''), forms_list))
        if not match_list:
            continue
        else:
            return True
    return False


def get_pre_admission(driver, mrn_data):
    """
    pre-admission/pa - if completed - PA_is_signed__c='Yes'
    rtc attachment - if completed - RTC_is_signed__c='Yes'
    """
    href = driver.find_element(By.LINK_TEXT, "Pre-Admission").get_attribute("href")
    tab_data = get_forms_from_tab(
        driver=driver,
        href=href,
        form_name_list=['pre-admission assessment',
                        'pa assessment'
                        ' pa ',
                        'rtc attachment',
                        ' rtc ']
    )

    if not form_present(tab_data, ['pre-admission assessment', 'pa assessment', ' pa ']):
        mrn_data['form_missing'].append('pre-admission assessment')
    if not form_present(tab_data, ['rtc attachment', ' rtc ']):
        mrn_data['form_missing'].append('rtc')
    for row in tab_data:
        """ Correction - If in-progress, open, or missing then No else Yes"""
        if 'pre-admission assessment' in row['Document_Name__c'] or 'pa' in row['Document_Name__c']:
            if 'progress' not in row['status'] and 'open' not in row['status']:
                mrn_data['PA_is_signed__c'] = 'Yes'
            else:
                mrn_data['open_forms'].append('pre-admission assessment : ' + row['status'])
        elif 'rtc' in row['Document_Name__c']:
            if 'progress' not in row['status'] and 'open' not in row['status']:
                mrn_data['RTC_is_signed__c'] = 'Yes'
            else:
                mrn_data['open_forms'].append('rtc : ' + row['status'])
    if 'PA_is_signed__c' not in mrn_data:
        mrn_data['PA_is_signed__c'] = 'No'
    if 'RTC_is_signed__c' not in mrn_data:
        mrn_data['RTC_is_signed__c'] = 'No'


def get_information_tab(driver, cur_row):
    href = driver.find_element(By.LINK_TEXT, "Information").get_attribute("href")
    driver.get(href)
    rows = driver.find_elements("xpath", "//*[@class='grid_index']/tbody/tr")
    admit_time = rows[0].find_element("xpath", "//*[contains(text(),'Admission Date')]") \
        .find_element("xpath", "./../..").text.split("\n")
    cur_row['Admit_Date_Time__c'] = format_date_time(admit_time[len(admit_time) - 1])


def consent_tab(driver, mrn_data):
    href = driver.find_element(By.LINK_TEXT, "Consents").get_attribute("href")
    tab_data = get_forms_from_tab(
        driver=driver,
        href=href,
        form_name_list=['commitment to treatment']
    )
    if not form_present(tab_data, ['commitment to treatment']):
        mrn_data['form_missing'].append('commitment to treatment')
    for row in tab_data:
        if 'commitment to treatment' in row['Document_Name__c']:
            if 'signed' in row['status']:
                driver.get(row['link'])
                root_element = driver.find_elements(By.CLASS_NAME, 'border_top')[0]
                mrn_data['Intake_Consents_date__c'] = to_date_no_timezone(root_element.text.split(', ')[1],
                                                                          '%m/%d/%Y %H:%M %p')
                all_txt = root_element.find_element("xpath", "./../..").text.split("\n")
                staff_txt = all_txt[len(all_txt) - 1].split(':')[1].strip()
                mrn_data['Intake_Consents_completed_by__c'] = staff_txt
            else:
                mrn_data['open_forms'].append('commitment to treatment : ' + row['status'])


def intake_and_orientation(driver, mrn_data):
    href = driver.find_element(By.LINK_TEXT, "Intake & Orientation").get_attribute("href")
    tab_data = get_forms_from_tab(
        driver=driver,
        href=href,
        form_name_list=[
            'orientation sign-off',
            'orientation checklist',
            'client personal items and valuables'
        ]
    )
    if not form_present(tab_data, ['orientation sign-off', 'orientation checklist']):
        mrn_data['form_missing'].append('orientation sign-off')
    if not form_present(tab_data, ['client personal items and valuables']):
        mrn_data['form_missing'].append('client personal items and valuables')
    for row in tab_data:
        if 'orientation sign-off' in row['Document_Name__c'] or 'orientation checklist' in row['Document_Name__c']:
            if 'open' in row['status'] or 'progress' in row['status']:
                mrn_data['open_forms'].append('orientation sign-off : ' + row['status'])
            else:
                driver.get(row['link'])
                root_elements = driver.find_elements(By.CLASS_NAME, 'border_top')
                for sign_element in root_elements:
                    sign_text = sign_element.text
                    if 'Staff' in sign_text or 'staff' in sign_text:
                        sign_row_split = sign_text.split(",")
                        mrn_data['Intake_Orientation_Completed_By__c'] = sign_row_split[0]
                        mrn_data['Intake_Orientation_Date_Completed__c'] = to_date_no_timezone(
                            sign_row_split[len(sign_row_split) - 1].strip(), '%m/%d/%Y %I:%M %p'

                        )
        elif 'client personal items and valuables' in row['Document_Name__c']:
            if 'open' in row['status'] or 'progress' in row['status']:
                mrn_data['open_forms'].append('client personal items and valuables : ' + row['status'])
            else:
                driver.get(row['link'])
                sign_text = driver.find_elements(By.CLASS_NAME, 'border_top')[1].text
                if 'Staff' in sign_text or 'staff' in sign_text:
                    sign_row_split = sign_text.split(",")
                    mrn_data['Client_PI_V_Completed_By__c'] = sign_row_split[0]
                    mrn_data['Client_PI_V_Date_Completed__c'] = to_date_no_timezone(
                        sign_row_split[len(sign_row_split) - 1].strip(), '%m/%d/%Y %I:%M %p'

                    )


def medical(driver, mrn_data):
    href = driver.find_element(By.LINK_TEXT, "Medical").get_attribute("href")
    tab_data = get_forms_from_tab(
        driver=driver,
        href=href,
        form_name_list=[
            'face to face',
            'f2f',
            'attachment h&p evaluation',
            'attachment h&p',
            'attachment h & p',
            'attachment history and physical',
            'h&p evaluation',
            'h&p',
            'psychiatric eval'
        ]
    )
    if not form_present(tab_data, ['face to face', 'f2f']):
        mrn_data['form_missing'].append('f2f')
    if not form_present(tab_data, ['attachment h&p evaluation', 'attachment h&p',
                                   'attachment history and physical',
                                   'h&p evaluation',
                                   'h&p']):
        mrn_data['form_missing'].append('h&p')
    if not form_present(tab_data, ['psychiatric eval']):
        mrn_data['form_missing'].append('psychiatric eval')
    for row in tab_data:
        if 'face to face' in row['Document_Name__c'] or 'f2f' in row['Document_Name__c']:
            if 'open' in row['status'] or 'progress' in row['status']:
                mrn_data['open_forms'].append('f2f : ' + row['status'])
            else:
                driver.get(row['link'])
                sign_text = driver.find_elements(By.CLASS_NAME, 'border_top')[0].text
                if 'Staff' in sign_text or 'staff' in sign_text:
                    sign_row_split = sign_text.split(",")
                    mrn_data['F2F_completed_by__c'] = sign_row_split[0]
                    mrn_data['Face_2_Face_2_0_Form__c'] = 'Yes'
                    mrn_data['F2F_Date_Completed__c'] = to_date_no_timezone(
                        sign_row_split[len(sign_row_split) - 1].strip(), '%m/%d/%Y %I:%M %p'

                    )
                    mrn_data['Columbia_Triage_completed_by__c'] = mrn_data['F2F_completed_by__c']
                    mrn_data['Columbia_Triage_Screen_date_completed__c'] = mrn_data['F2F_Date_Completed__c']
                    mrn_data['Pain_Screen_completed_by__c'] = mrn_data['F2F_completed_by__c']
                    mrn_data['Pain_Screen_date_completed__c'] = mrn_data['F2F_Date_Completed__c']
                    mrn_data['Nutritional_Screen_date_completed__c'] = mrn_data['F2F_Date_Completed__c']
                    mrn_data['Nutritional_Screen_completed_by__c'] = mrn_data['F2F_completed_by__c']
        elif (
                'attachment h&p evaluation' in row['Document_Name__c'] or
                'attachment h&p' in row['Document_Name__c'] or
                'attachment history and physical' in row['Document_Name__c'] or
                'h&p evaluation' in row['Document_Name__c'] or
                'h&p' in row['Document_Name__c']
        ):
            if 'open' in row['status'] or 'progress' in row['status']:
                mrn_data['open_forms'].append('h&p : ' + row['status'])
            else:
                driver.get(row['link'])
                sign_text = driver.find_elements(By.CLASS_NAME, 'border_top')[0].text
                if 'Staff' in sign_text or 'staff' in sign_text:
                    sign_row_split = sign_text.split(",")
                    mrn_data['H_P_is_completed_by__c'] = sign_row_split[0]
                    mrn_data['H_P_date_completed__c'] = to_date_no_timezone(
                        sign_row_split[len(sign_row_split) - 1].strip(), '%m/%d/%Y %I:%M %p'

                    )

        elif 'psychiatric eval' in row['Document_Name__c']:
            if 'open' in row['status'] or 'progress' in row['status']:
                mrn_data['open_forms'].append('psychiatric eval : ' + row['status'])
            else:
                # Vijay - multiple dates form available
                driver.get(row['link'])
                sign_text = driver.find_elements(By.CLASS_NAME, 'border_top')[0].text
                if 'Staff' in sign_text or 'staff' in sign_text:
                    sign_row_split = sign_text.split(",")
                    mrn_data['Psych_Eval_is_completed_by__c'] = sign_row_split[0]
                    mrn_data['Psych_Eval_data_completed__c'] = to_date_no_timezone(
                        sign_row_split[len(sign_row_split) - 1].strip(), '%m/%d/%Y %I:%M %p'

                    )


def clinical(driver, mrn_data):
    href = driver.find_element(By.LINK_TEXT, "Clinical").get_attribute("href")
    tab_data = get_forms_from_tab(
        driver=driver,
        href=href,
        form_name_list=[
            'biopsychosocial assessment',
            'bps'
        ]
    )
    if not form_present(tab_data, ['biopsychosocial assessment', 'bps']):
        mrn_data['form_missing'].append('bps')
    for row in tab_data:
        # Vijay - will this form be created for multiple dates with this name?
        # Vijay - check possible names for h&p form
        if 'biopsychosocial assessment' in row['Document_Name__c'] or 'bps' in row['Document_Name__c']:
            if 'open' in row['status'] or 'progress' in row['status']:
                mrn_data['open_forms'].append('bps : ' + row['status'])
            else:
                driver.get(row['link'])
                root_elements = driver.find_elements(By.CLASS_NAME, 'border_top')
                for sign_element in root_elements:
                    sign_text = sign_element.text
                    if 'Staff' in sign_text or 'staff' in sign_text:
                        sign_row_split = sign_text.split(",")
                        mrn_data['BPS_is_completed_by__c'] = sign_row_split[0]
                        mrn_data['BPS_date_completed__c'] = to_date_no_timezone(
                            sign_row_split[len(sign_row_split) - 1].strip(), '%m/%d/%Y %I:%M %p'

                        )
                    if 'Review' in sign_text or 'review' in sign_text:
                        sign_row_split = sign_text.split(",")
                        mrn_data['BPS_Supervisor_is_signed_off_by__c'] = sign_row_split[0]
                        mrn_data['BPS_Supervisor_date_completed__c'] = to_date_no_timezone(
                            sign_row_split[len(sign_row_split) - 1].strip(), '%m/%d/%Y %I:%M %p'

                        )


def treatment_plans(driver, mrn_data, processing_date):
    href = driver.find_element(By.LINK_TEXT, "Treatment Plans").get_attribute("href")
    tab_data = get_forms_from_tab(
        driver=driver,
        href=href,
        form_name_list=[
            'initial treatment plan',
            'master problem list'
        ]
    )
    if not form_present(tab_data, ['initial treatment plan']):
        mrn_data['form_missing'].append('initial treatment plan')
    if not form_present(tab_data, ['master problem list']):
        mrn_data['form_missing'].append('master problem list')
    for row in tab_data:
        if 'initial treatment plan' in row['Document_Name__c']:
            if 'open' in row['status'] or 'progress' in row['status']:
                mrn_data['open_forms'].append('initial treatment plan : ' + row['status'])
            else:
                driver.get(row['link'])
                root_elements = driver.find_elements(By.CLASS_NAME, 'border_top')
                if len(root_elements) > 1:
                    sign_text = root_elements[1].text
                    sign_row_split = sign_text.split(",")
                    mrn_data['ITP_completed_by__c'] = sign_row_split[0]
                    mrn_data['ITP_date_completed__c'] = to_date_no_timezone(
                        sign_row_split[len(sign_row_split) - 1].strip(), '%m/%d/%Y %I:%M %p'
                    )
                if len(root_elements) > 2:
                    sign_text = root_elements[2].text
                    sign_row_split = sign_text.split(",")
                    mrn_data['ITP_Supervisor_is_signed_off_by__c'] = sign_row_split[0]
                    mrn_data['ITP_Supervisor_date_completed__c'] = to_date_no_timezone(
                        sign_row_split[len(sign_row_split) - 1].strip(), '%m/%d/%Y %I:%M %p'
                    )
                tabls = driver.find_elements("xpath", "//*[@class='_65 right']")
                """
                today or past and attained - yes
                future and irrespective of status - yes
                today or past and open - no
                so all objectives should be yes to set ITP_target_date_was_updated__c = 'Yes' 
                """
                """ Correction - ITP_target_date_was_updated__c not updated"""
                itp_target_date_updated = []
                for tabl in tabls:
                    tabl_text = tabl.text
                    if 'Open' in tabl_text or 'Attained' in tabl_text or 'Cancelled' in tabl_text:
                        objective_rows = tabl_text.split('\n')
                        objective_cols = objective_rows[len(objective_rows) - 1].split(' ')
                        target_date = datetime.strptime(objective_cols[0], '%m/%d/%Y')  # date format 06/09/2021
                        target_status = objective_cols[1]
                        if 'Cancelled' in target_status:
                            itp_target_date_updated.append(True)
                        elif target_date <= processing_date and 'Attained' in target_status:
                            itp_target_date_updated.append(True)
                        elif target_date > processing_date:
                            itp_target_date_updated.append(True)
                        else:
                            itp_target_date_updated.append(False)
                if all(itp_target_date_updated):
                    mrn_data['ITP_target_date_was_updated__c'] = 'Yes'
                else:
                    mrn_data['ITP_target_date_was_updated__c'] = 'No'

        elif 'master problem list' in row['Document_Name__c']:
            if 'open' in row['status'] or 'progress' in row['status']:
                mrn_data['open_forms'].append('master problem list : ' + row['status'])
            else:
                driver.get(row['link'])
                root_elements = driver.find_elements(By.CLASS_NAME, 'border_top')
                for sign_element in root_elements:
                    sign_text = sign_element.text
                    if 'Staff' in sign_text or 'staff' in sign_text:
                        sign_row_split = sign_text.split(",")
                        mrn_data['MTP_completed_by__c'] = sign_row_split[0]
                        mrn_data['MTP_date_completed__c'] = to_date_no_timezone(
                            sign_row_split[len(sign_row_split) - 1].strip(), '%m/%d/%Y %I:%M %p'
                        )
                    if 'Review' in sign_text or 'review' in sign_text:
                        sign_row_split = sign_text.split(",")
                        mrn_data['MTP_Supervisor_is_signed_off_by__c'] = sign_row_split[0]
                        mrn_data['MTP_Supervisor_date_completed__c'] = to_date_no_timezone(
                            sign_row_split[len(sign_row_split) - 1].strip(), '%m/%d/%Y %I:%M %p'
                        )


def get_mrn_data(patient_id, driver, mrn_number, processing_date, base_url, processing_centre):
    print('Processing {}'.format(mrn_number))
    mrn_data = {'Medical_Record_Number__c': mrn_number, 'centre': processing_centre,
                'open_forms': [], 'form_missing': []}
    get_pre_admission(driver, mrn_data)
    get_information_tab(driver, mrn_data)
    consent_tab(driver, mrn_data)
    intake_and_orientation(driver, mrn_data)
    medical(driver, mrn_data)
    clinical(driver, mrn_data)
    treatment_plans(driver, mrn_data, processing_date)
    return mrn_data


def prepare_df_for_salesforce(temp_df, list_columns):
    df = temp_df
    for x in list_columns:
        if x not in list(df.columns.values):
            df[x] = float('nan')
    open_status = df[(df['open_forms'].str.len() > 0) | (df['form_missing'].str.len() > 0) | (df.isna().any(axis=1))]
    salesforce_df = temp_df
    salesforce_df = salesforce_df[salesforce_df.columns.intersection(list_columns)]
    return open_status, salesforce_df


def main():
    kipu_util = KipuUtility(sys.argv[1])
    configs = kipu_util.get_config()
    browser = BrowserUtility.get_browser()
    base_url, selected_centre = kipu_util.login(browser)

    print("Enter csv file path")
    csv_filename = input()
    print("Enter processing date in mm/dd/yyyy e.g. 05/31/2023")
    entered_date = input()
    #   Actual Processing by reading each row in CSV
    entered_date = datetime.strptime(entered_date, '%m/%d/%Y')
    mr_patient_rows = []
    failed_mrn = []
    with open(csv_filename, "rt", encoding='ascii') as infile:
        read = csv.DictReader(infile)
        for row in read:
            try:
                start_time = time()
                patient_id = kipu_util.search_mrn(browser, row['Medical Record Number'], base_url)
                mrn_row_data = get_mrn_data(patient_id, browser, row['Medical Record Number'], entered_date, base_url,
                                            selected_centre)
                print("--- For %s took %s seconds for processing ---" % (
                row['Medical Record Number'], time() - start_time))
                if mrn_row_data:
                    mr_patient_rows.append(mrn_row_data)
            except Exception as e:
                traceback.print_exc()
                failed_mrn.append(row['Medical Record Number'])

    # write data to temporary csv file
    print("Got data from Kipu....")

    df = pandas.DataFrame.from_dict(mr_patient_rows)
    print(*mr_patient_rows, sep='\n')
    open_status_df, salesforce_df = prepare_df_for_salesforce(
        df, list_columns=[
            'PA_is_signed__c',
            'RTC_is_signed__c',
            'Admit_Date_Time__c',
            'Intake_Consents_date__c',
            'Intake_Consents_completed_by__c',
            'Intake_Orientation_Date_Completed__c',
            'Intake_Orientation_Completed_By__c',
            'Client_PI_V_Completed_By__c',
            'Client_PI_V_Date_Completed__c',
            'F2F_completed_by__c',
            'Face_2_Face_2_0_Form__c',
            'F2F_Date_Completed__c',
            'Columbia_Triage_completed_by__c',
            'Columbia_Triage_Screen_date_completed__c',
            'Pain_Screen_completed_by__c',
            'Pain_Screen_date_completed__c',
            'Nutritional_Screen_date_completed__c',
            'Nutritional_Screen_completed_by__c',
            'H_P_is_completed_by__c',
            'H_P_date_completed__c',
            'Psych_Eval_is_completed_by__c',
            'Psych_Eval_data_completed__c',
            'BPS_is_completed_by__c',
            'BPS_date_completed__c',
            'BPS_Supervisor_is_signed_off_by__c',
            'BPS_Supervisor_date_completed__c',
            'ITP_completed_by__c',
            'ITP_date_completed__c',
            'ITP_Supervisor_is_signed_off_by__c',
            'ITP_Supervisor_date_completed__c',
            'MTP_completed_by__c',
            'MTP_date_completed__c',
            'MTP_Supervisor_date_completed__c',
            'MTP_Supervisor_is_signed_off_by__c',
            'Medical_Record_Number__c'
        ]
    )
    del df
    print("Total MRN for with no or partial data {}\n{}".format(
        open_status_df.shape[0],
        open_status_df['Medical_Record_Number__c'].values))
    print("Total failed to get data {}\n{}\n".format(len(failed_mrn), failed_mrn))

    if salesforce_df is None:
        print("Total rowcount to insert in Salesforce is 0")
        browser.quit()
        sys.exit(0)
    print("Total rowcount to insert in Salesforce is {}".format(salesforce_df.shape[0]))
    print("Insert to Salesforce [y/n]")
    is_insert = input().casefold()  # lowercase
    if is_insert == 'y' or is_insert == 'yes':
        sf = SalesForceUpsertUtility('KIPU_Chart_Audit__c', 'Medical_Record_Number__c')
        is_written = sf.write_records_using_conf(salesforce_df, configs)
    if open_status_df.shape[0] > 0:
        print('Enter filename with path to write missing data')
        open_path = input()
        open_status_df.to_csv(open_path)
    else:
        print("No Open or Missing Forms present")
    browser.quit()


main()

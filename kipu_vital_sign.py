import csv
import os
import sys
from datetime import datetime
from time import time

import pandas
from selenium.webdriver.common.by import By

from kipuUtils.BrowserUtility import BrowserUtility
from kipuUtils.KipuUtility import KipuUtility
from kipuUtils.SalesForceInsertUtility import SalesForceInsertUtility


def to_date_no_timezone(date_str, format='%m/%d/%Y', return_format='%Y-%m-%dT%H:%M:%S.000Z'):
    return datetime.strptime(date_str, format).date().strftime(return_format)


def get_tab_level_data(browser_ptr, href, mr_number, processing_date, processing_centre, kipu_chart_audit__c):
    browser_ptr.get(href)
    rows = browser_ptr.find_elements("xpath", "//*[@class='grid_index']/tbody/tr")
    am_found = pm_found = False
    tab_data = []
    cur_row = {'mrn': mr_number, 'key': 'value', 'KIPU_Chart_Audit__c': kipu_chart_audit__c,
               'Vital_Sign_Date': to_date_no_timezone(processing_date, '%m/%d/%Y', '%Y-%m-%d')}
    processing_date = to_date_no_timezone(processing_date, '%m/%d/%Y', '%m/%d/%y')
    for row in rows:
        row_txt = row.text
        if processing_date in row_txt:
            if ' AM ' in row_txt:
                am_found = True
            elif ' PM ' in row_txt:
                pm_found = True
        if am_found and pm_found:
            break
    if am_found and pm_found:
        cur_row['Vital_Sign_Status__C'] = 'Both Completed'
    elif not am_found and not pm_found:
        cur_row['Vital_Sign_Status__C'] = 'Both Missed'
    elif am_found:
        cur_row['Vital_Sign_Status__C'] = 'PM Missed'
    else:
        cur_row['Vital_Sign_Status__C'] = 'AM Missed'
    tab_data.append(cur_row)
    return tab_data


def get_mrn_data(patient_id, browser_ptr, mrn_number, processing_date, base_url, selected_centre, kipu_chart_audit__c):
    print('Processing {}'.format(mrn_number))
    # Process Clinical and Progress Tab
    href = '{}/patients/{}/vital_signs'.format(base_url, patient_id)
    return get_tab_level_data(browser_ptr, href, mrn_number, processing_date, selected_centre, kipu_chart_audit__c)


def prepare_df_for_salesforce(temp_df, list_columns):
    if 'link' in temp_df:
        open_status = temp_df[temp_df['link'].isna()]
    else:
        return temp_df, None
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
    mr_patient_rows = []
    failed_mrn = []
    with open(csv_filename, "rt", encoding='ascii') as infile:
        read = csv.DictReader(infile)
        for row in read:
            try:
                start_time = time()
                patient_id = kipu_util.search_mrn(browser, row['KIPU MRN'], base_url)
                if not patient_id:
                    continue
                mrn_row_data = get_mrn_data(patient_id, browser, row['KIPU MRN'], entered_date, base_url,
                                            selected_centre,
                                            row['KIPU Chart Audit: ID'])
                print("--- For %s took %s seconds for processing ---" % (row['KIPU MRN'], time() - start_time))
                if mrn_row_data:
                    mr_patient_rows.extend(mrn_row_data)
            except Exception as e:
                print(e)
                failed_mrn.append(row['KIPU MRN'])
    if not mr_patient_rows:
        print("No Patient Data Found")
        browser.quit()
        sys.exit(1)

    # write data to temporary csv file
    print("Got data from Kipu....")
    salesforce_df = pandas.DataFrame.from_dict(mr_patient_rows)
    print(*mr_patient_rows, sep='\n')
    print("Total failed to get data {}\n{}\n".format(len(failed_mrn), failed_mrn))
    print("Total rowcount to insert in Salesforce is {}".format(salesforce_df.shape[0]))
    print("Insert to Salesforce [y/n]")
    is_insert = input().casefold()  # lowercase
    if is_insert != 'y' and is_insert != 'yes':
        browser.quit()
        sys.exit(0)
    sf = SalesForceInsertUtility('KIPU_Audit_Vital_Signs__c')
    is_written = sf.write_records_using_conf(salesforce_df, configs)
    browser.quit()


main()

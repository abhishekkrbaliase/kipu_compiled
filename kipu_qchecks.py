# import csv
import logging
import os
import sys
import traceback
from datetime import datetime, timedelta
from time import time

import pandas
from selenium.webdriver.common.by import By

from kipuUtils.BrowserUtility import BrowserUtility
from kipuUtils.KipuUtility import KipuUtility
from kipuUtils.SalesForceInsertUtility import SalesForceInsertUtility

logger = logging.getLogger('kipu_qcheck')


def get_24_hour_time(s):
    "".join(s.split())
    hour = int(s[:2]) % 12
    minutes = int(s[3:5])
    if s[5] == 'P' or s[5] == 'p':
        hour += 12
    return "{:02}:{:02}".format(hour, minutes)


def get_delta_minutes_allowed(centre_code):
    if 'CC' == centre_code or 'MS' == centre_code:
        return 7
    else:
        return 5


def get_mr_patient_rows(q_check_id, q_start, q_end, header_str, footer_str, centre_code, row_time_intervals):
    logger.debug("header_str\n" + header_str)
    logger.debug("footer_str\n" + footer_str)
    split_header = header_str.replace('\nOFF-PREMISE', '').replace('\n', ' ').split(' ')
    len_split_header = len(split_header)
    split_footer = footer_str.replace("\nOFF-PREMISE", '').replace('\n', ' ').replace(", ", ",").split(' ')
    logger.debug(" Got Due Time from UI {}".format(' '.join(split_header)))
    logger.debug(" Got Completed Time from UI {}".format(' '.join(split_footer)))
    due_time = []
    completed_time = []
    index = 0
    while index < len_split_header:
        if not split_header[index] and not split_footer[index]:
            index = index + 1
            continue
        if not split_header[index]:  # if header is not present but footer is
            split_header.insert(index, '')
            split_header.insert(index, '')
            index = index + 3
            continue
        elif not split_footer[index]:  # if footer is not present but header is
            split_footer.insert(index, '')
            split_footer.insert(index, '')
            index = index + 3
            continue

        due_time.append(datetime.strptime(get_24_hour_time(split_header[index] + split_header[index + 1]), '%H:%M'))
        split_footer_staff_time = split_footer[index].split(",")
        staff_name = split_footer_staff_time[0]
        completed_time.append((staff_name,
                               datetime.strptime(get_24_hour_time(split_footer_staff_time[1] + split_footer[index + 1]),
                                                 '%H:%M')))
        index = index + 3
    q_current = q_start = datetime.strptime(get_24_hour_time(q_start), '%H:%M')
    q_end = datetime.strptime(get_24_hour_time(q_end), '%H:%M')
    if datetime.strptime("1900-01-01", "%Y-%m-%d") == q_end:
        q_end = q_end + timedelta(days=1)

    # if cur_index is the time due, it is not Detox_Check_Missed__c
    rows = []
    index = 0
    prev_user_checked = None
    delta_minutes_allowed = get_delta_minutes_allowed(centre_code)

    logger.debug(str(due_time))
    logger.debug(len(due_time))
    logger.debug(str(completed_time))
    logger.debug(len(completed_time))
    while index < len(due_time) and due_time[index] < q_start:
        index = index + 1
        continue

    # remove duplicates in due_time
    while index < len(due_time) - 2:
        if due_time[index] == due_time[index + 1]:
            if abs(completed_time[index][1] - due_time[index]) <= timedelta(minutes=delta_minutes_allowed):
                del due_time[index + 1]
                del completed_time[index + 1]
            else:
                del due_time[index]
                del completed_time[index]
        else:
            index = index + 1
    index = 0
    # If last index and first index is of next day or previous day respectively and not same day
    # e.g. 12AM due and completed at 11:58PM of previous day
    time_12_am = datetime.strptime(get_24_hour_time("12:00AM"), '%H:%M')
    while index < len(due_time):
        if due_time[index] == time_12_am:
            break
        index = index + 1
    if index == len(due_time):
        index = 0
    while index > -1:
        if abs((completed_time[index][1] - timedelta(days=1)) - due_time[index]) <= timedelta(
                minutes=delta_minutes_allowed):
            completed_time[index] = (completed_time[index][0], completed_time[index][1] - timedelta(days=1))
        index = index - 1
    index = 0
    # iterate through all the time intervals
    while q_end >= q_current:
        row_dict = {"House_Locations__c": "", "KIPU_Audit_Q_Checks__c": q_check_id}
        if index < len(due_time) and q_current == due_time[index]:
            max_time_allowed = due_time[index] + timedelta(minutes=delta_minutes_allowed)
            min_time_allowed = due_time[index] + timedelta(minutes=(-1 * delta_minutes_allowed))
            logger.debug(str(min_time_allowed) + " " + str(completed_time[index][1]) + " " + str(max_time_allowed))
            prev_user_checked = completed_time[index][0]
            if min_time_allowed <= completed_time[index][1] <= max_time_allowed:  # fix for 12 AM / 12PM
                q_current = q_current + timedelta(minutes=row_time_intervals)
                index = index + 1
                continue  # Enter only records which is beyond time allowed
            row_dict["Detox_Check_Missed__c"] = "False"
            row_dict["Staff_Who_Missed_Detox_Check__c"] = completed_time[index][0]
            row_dict["Time_Detox_Check_Was_Completed__c"] = completed_time[index][1].strftime("%H:%M:%S.000Z")
            row_dict["Time_Detox_Check_Was_Due__c"] = due_time[index].strftime("%H:%M:%S.000Z")
            index = index + 1  # Increment index if current_time==due_time
        else:
            if prev_user_checked:
                row_dict["Staff_Who_Missed_Detox_Check__c"] = prev_user_checked
            elif index < len(due_time):
                row_dict["Staff_Who_Missed_Detox_Check__c"] = completed_time[index][0]  # get next User who checked
            row_dict["Detox_Check_Missed__c"] = "True"
            row_dict["Time_Detox_Check_Was_Completed__c"] = ""
            row_dict["Time_Detox_Check_Was_Due__c"] = q_current.strftime("%H:%M:%S.000Z")
        rows.append(row_dict)
        q_current = q_current + timedelta(minutes=row_time_intervals)
    if not rows or len(rows) == 0:
        print("No Bad Records found")
    return rows


def get_date_from_string(processed_date_str):
    datetime_object = datetime.strptime(processed_date_str, '%m/%d/%Y')
    datetime_object_minus_1 = datetime_object - timedelta(days=1)
    if 'nt' in os.name:
        return datetime_object.strftime('%A, %b %#d, %Y'), datetime_object_minus_1.strftime('%A, %b %#d, %Y')
    return datetime_object.strftime('%A, %b %-d, %Y'), datetime_object_minus_1.strftime('%A, %b %-d, %Y')


def process_mrn_records(qcheck_id, browser_ptr, href, qcheck_start, qcheck_end, centre_code, row_time_intervals):
    browser_ptr.get(href)  # abhishek  - check loading of page
    total_rows = len(browser_ptr.find_element(By.CLASS_NAME, "compressed").find_elements(By.TAG_NAME, "tr"))
    header_cols = browser_ptr.find_element(By.CLASS_NAME, "compressed").find_elements(By.TAG_NAME, "tr")[
        0].find_elements(By.TAG_NAME, "th")
    footer_cols = browser_ptr.find_element(By.CLASS_NAME, "compressed").find_elements(By.TAG_NAME, "tr")[
        total_rows - 1].find_elements(By.TAG_NAME, "td")
    index = 0
    header = ''
    footer = ''
    while index < len(header_cols):
        if not header_cols[index].text and not footer_cols[index].text:
            index = index + 1
            continue
        header = header + '\n' + header_cols[index].text
        footer = footer + '\n' + footer_cols[index].text
        index = index + 1

    return get_mr_patient_rows(qcheck_id, qcheck_start, qcheck_end, header, footer, centre_code, row_time_intervals)


def get_tab_level_data(
        driver, q30check_id, q30check_start, q30check_end,
        q15check_id, q15check_start, q15check_end, processing_date, centre_code):
    dates_entered = get_date_from_string(processing_date)
    process_date = dates_entered[0]
    process_date_minus_1 = dates_entered[1]
    tr = driver.find_elements(By.TAG_NAME, "tr")
    index = 0
    start_index = 0
    end_index = 0
    for t in tr:
        index = index + 1
        if process_date in t.text:
            start_index = index
        elif process_date_minus_1 in t.text:
            end_index = index
            break
    if end_index < start_index:
        end_index = len(tr) - 1  # in case there is no t-1 date
    href_q30 = None
    href_q15 = None
    rows = []
    while start_index < end_index:
        if 'Detox Flowsheet Q30' in tr[start_index].text:
            tmp_href = tr[start_index].find_element(By.LINK_TEXT, "Detox Flowsheet Q30")
            if 'edit?process' not in tmp_href.get_attribute("href"):
                href_q30 = tmp_href.get_attribute("href")
        elif 'Detox Check Q30' in tr[start_index].text:
            tmp_href = tr[start_index].find_element(By.LINK_TEXT, "Detox Check Q30")
            if 'edit?process' not in tmp_href.get_attribute("href"):
                href_q30 = tmp_href.get_attribute("href")
        if 'Detox Check Q15' in tr[start_index].text:
            tmp_href = tr[start_index].find_element(By.LINK_TEXT,
                                                    "Detox Check Q15")  # abhishek - check for exact text
            if 'edit?process' not in tmp_href.get_attribute("href"):
                href_q15 = tmp_href.get_attribute("href")
        elif 'Detox Flowsheet Q15' in tr[start_index].text:
            tmp_href = tr[start_index].find_element(By.LINK_TEXT,
                                                    "Detox Flowsheet Q15")  # abhishek - check for exact text
            if 'edit?process' not in tmp_href.get_attribute("href"):
                href_q15 = tmp_href.get_attribute("href")
        start_index = start_index + 1

    if not href_q30 and not href_q15:
        print("Q30 and Q15 not found")

    if href_q30:
        rows.extend(
            process_mrn_records(q30check_id, driver, href_q30, q30check_start, q30check_end, centre_code, 30))

    if href_q15:
        rows.extend(
            process_mrn_records(q15check_id, driver, href_q15, q15check_start, q15check_end, centre_code, 15))
    return rows


def get_mrn_data(
        patient_id, driver, mrn_number, q30check_id, q30check_start, q30check_end,
        q15check_id, q15check_start, q15check_end, processing_date, selected_centre):
    if not patient_id:
        return None
    print('Processing {}'.format(mrn_number))
    # Process Clinical and Progress Tab
    tab_href = driver.find_element(By.LINK_TEXT, "Daily Updates").get_attribute("href")
    driver.get(tab_href)
    return get_tab_level_data(
        driver=driver,
        q30check_id=q30check_id,
        q30check_start=q30check_start,
        q30check_end=q30check_end,
        q15check_id=q15check_id,
        q15check_start=q15check_start,
        q15check_end=q15check_end,
        processing_date=processing_date,
        centre_code=selected_centre
    )


#
# def prepare_df_for_salesforce(temp_df, list_columns):
#     if 'link' in temp_df:
#         open_status = temp_df[temp_df['link'].isna()]
#     else:
#         return temp_df, None
#     salesforce_df = temp_df[temp_df['link'].notna()]
#     salesforce_df = salesforce_df[salesforce_df.columns.intersection(list_columns)]
#     return open_status, salesforce_df


def get_val_for_key(row, key):
    if key in row:
        return row[key]
    return None


def extract_time(date_time_str):
    if not date_time_str:
        return None
    d_split = date_time_str.strip().split(" ")
    if len(d_split[len(d_split) - 2]) < 5:
        return '0' + d_split[len(d_split) - 2] + d_split[len(d_split) - 1]
    return d_split[len(d_split) - 2] + d_split[len(d_split) - 1]


def main():
    kipu_util = KipuUtility(sys.argv[1])
    configs = kipu_util.get_config()
    browser = BrowserUtility.get_browser()
    base_url, selected_centre = kipu_util.login(browser)

    print("Enter csv file path")
    csv_filename = input()
    print("Enter date in mm/dd/yyyy e.g. 05/31/2023")
    processed_date_str = input()
    #   Actual Processing by reading each row in CSV
    mr_patient_rows = []
    failed_mrn = []
    df = pandas.read_csv(csv_filename)
    q30 = df[df['Type of Q-Check'] == 'Q-30']
    q15 = df[df['Type of Q-Check'] == 'Q-15']
    merged_df = pandas.DataFrame.merge(q30, q15, on=['Medical Record Number'], how='outer')
    del df
    del q15
    del q30
    merged_df = merged_df.where(pandas.notnull(merged_df), None)
    for i in range(len(merged_df)):
        q30check_id = merged_df.loc[i, "KIPU Audit - Q Checks: ID_x"]
        q30check_start = extract_time(merged_df.loc[i, "Q30 Detox Check Start Date and Time_x"])
        q30check_end = extract_time(merged_df.loc[i, "Q30 Detox Check End Date and Time_x"])
        q15check_start = extract_time(merged_df.loc[i, "Q30 Detox Check Start Date and Time_y"])
        q15check_end = extract_time(merged_df.loc[i, "Q30 Detox Check End Date and Time_y"])
        q15check_id = merged_df.loc[i, "KIPU Audit - Q Checks: ID_y"]
        mrn = merged_df.loc[i, "Medical Record Number"]
        try:
            start_time = time()
            patient_id = kipu_util.search_mrn(browser, mrn, base_url)
            mrn_row_data = get_mrn_data(
                patient_id=patient_id,
                driver=browser,
                mrn_number=mrn,
                q30check_id=q30check_id,
                q30check_start=q30check_start,
                q30check_end=q30check_end,
                q15check_id=q15check_id,
                q15check_start=q15check_start,
                q15check_end=q15check_end,
                processing_date=processed_date_str,
                selected_centre=selected_centre
            )
            print("--- For %s took %s seconds for processing ---" %
                  (mrn, time() - start_time))
            if mrn_row_data:
                mr_patient_rows.extend(mrn_row_data)
        except Exception as e:
            traceback.print_exc()
            failed_mrn.append(mrn)

    # with open(csv_filename, "rt", encoding='ascii') as infile:
    #     read = csv.DictReader(infile)
    #     for row in read:
    #         try:
    #             q30check_id = get_val_for_key(row, '')
    #             q30check_start = get_val_for_key(row, '')
    #             q30check_end = get_val_for_key(row, '')
    #             q15check_start = get_val_for_key(row, '')
    #             q15check_end = get_val_for_key(row, '')
    #             q15check_id = get_val_for_key(row, '')
    #             mrn = get_val_for_key(row, '')
    #             start_time = time()
    #             patient_id = kipu_util.search_mrn(browser, mrn, base_url)
    #             mrn_row_data = get_mrn_data(
    #                 patient_id=patient_id,
    #                 driver=browser,
    #                 mrn_number=mrn,
    #                 q30check_id=q30check_id,
    #                 q30check_start=q30check_start,
    #                 q30check_end=q30check_end,
    #                 q15check_id=q15check_id,
    #                 q15check_start=q15check_start,
    #                 q15check_end=q15check_end,
    #                 processing_date=processed_date_str,
    #                 selected_centre=selected_centre
    #             )
    #             print("--- For %s took %s seconds for processing ---" %
    #                   (mrn, time() - start_time))
    #             if mrn_row_data:
    #                 mr_patient_rows.append(mrn_row_data)
    #         except Exception as e:
    #             print(e)
    #             failed_mrn.append(mrn)

    # write data to temporary csv file
    print("Got data from Kipu....")

    salesforce_df = pandas.DataFrame.from_dict(mr_patient_rows)
    print(*mr_patient_rows, sep='\n')
    # open_status_df, salesforce_df = prepare_df_for_salesforce(
    #     df, list_columns=[
    #         'House_Locations__c',
    #         'KIPU_Audit_Q_Checks__c'
    #         'Detox_Check_Missed__c',
    #         'Staff_Who_Missed_Detox_Check__c',
    #         'Time_Detox_Check_Was_Completed__c'
    #         'Time_Detox_Check_Was_Due__c'
    #     ]
    # )
    # del df
    # print("Total 'In Progress' or 'Open' Status is {}".format(open_status_df.shape[0]))
    print("Total failed to get data {}\n{}\n".format(len(failed_mrn), failed_mrn))
    if salesforce_df is None or salesforce_df.shape[0] < 1:
        print("Total rowcount to insert in Salesforce is 0")
        browser.quit()
        sys.exit(0)
    print("Total rowcount to insert in Salesforce is {}".format(salesforce_df.shape[0]))
    print("Insert to Salesforce [y/n]")
    is_insert = input().casefold()  # lowercase
    if is_insert != 'y' and is_insert != 'yes':
        browser.quit()
        sys.exit(0)
    sf = SalesForceInsertUtility('KIPU_Audit_Q_Checks_Line_Items__c')
    is_written = sf.write_records_using_conf(salesforce_df, configs)
    browser.quit()


main()

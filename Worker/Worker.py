import azure.core.exceptions
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import Select
from selenium.webdriver.common.by import By
from azure.storage.queue import QueueClient
from azure.data.tables import TableServiceClient
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import time
import smtplib
import datetime
import logging
import threading
from Common import connect_str, queue_name, table_name, sender_address, sender_pass
logging.basicConfig(level=logging.INFO)

queue_run_once_client = QueueClient.from_connection_string(connect_str, queue_name)
table_service = TableServiceClient.from_connection_string(conn_str=connect_str)
table_client = table_service.get_table_client(table_name=table_name)


class DateChecker:

    def __init__(self, url, cool_down_time=5, max_threads=10):
        """
        Check available dates on website.
        :param url: URL of website to check dates for (str)
        :param cool_down_time: how often to check a continuous run request in minutes (int)
        """
        self.months = ['januari', 'februari', 'maart', 'april', 'mei', 'juni', 'juli', 'augustus', 'september',
                       'oktober', 'november', 'december']
        self.url = url
        self.cool_down_time = cool_down_time
        self.max_threads = max_threads
        self.threads = []

    def loop(self, from_queue=True, from_database=True):
        """
        Loop that checks for run requests (from queue or database).
        :param from_queue: check run once requests from message queue (Bool)
        :param from_database: check continuous run requests from database (Bool)
        """
        message_switch = True
        while True:
            if len(self.threads) >= self.max_threads:
                logging.warning("Maximum threads reached, not taking new requests")
            elif message_switch and from_queue:
                self._run_from_message()
            elif not message_switch and from_database:
                self._run_from_database()
            message_switch = not message_switch
            time.sleep(1)

    def check_available_dates(self, run_id, desired_months, desks, email=None, store_results=False):
        """
        Check all available dates in desired months on url.
        :return:
        """
        results = []
        # click_month_picker should be True initially and when the month picker element was clicked on the website,
        # because it disappears when clicked and should be reacquired in that case.
        click_month_picker = True
        driver = self._init_driver()
        driver.get(self.url)
        desk_dropdown = Select(driver.find_element(by=By.ID, value='desk'))
        desk_values = [option for option in desk_dropdown.options if option.text.lower() in desks]
        for desk_value in desk_values:
            if not desk_value:  # There's an empty option in the dropdown
                continue
            click_month_picker = self._check_desk_for_available_date(
                driver=driver, desk_value=desk_value, desk_dropdown=desk_dropdown,
                click_month_picker=click_month_picker, desired_months=desired_months, results=results)
        driver.quit()
        if results:
            if email:
                try:
                    self._mail_results(email=email, results=results)
                except Exception as e:
                    logging.error("Mailing results failed: {}".format(e))
            if store_results:
                self._store_results(results=results, run_id=run_id)
        return results

    def get_available_desks(self):

        try:
            entity = table_client.get_entity('Desks', '0')
            if datetime.datetime.now() - datetime.datetime.strptime(entity['LastRun'], '%d/%m/%Y %H:%M:%S') < \
               datetime.timedelta(minutes=60):
                return
            else:
                table_client.delete_entity(partition_key='Desks', row_key='0')
        except azure.core.exceptions.ResourceNotFoundError:
            pass
        self._get_available_desks()

    def _get_available_desks(self):

        driver = self._init_driver()
        driver.get(self.url)
        desks = self._get_desk_options(driver=driver)
        driver.quit()
        entity = {
            'PartitionKey': 'Desks',
            'RowKey': '0',
            'Desks': ','.join(desks),
            'CheckedAt': datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        }
        table_client.create_entity(entity)

    @staticmethod
    def _init_driver():
        """
        Start a headless chrome driver.
        """
        chrome_options = webdriver.ChromeOptions()
        chrome_options.headless = True
        return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

    @property
    def _database_requests(self):

        my_filter = "PartitionKey eq 'ContinuousRun'"
        return table_client.query_entities(my_filter)

    def _check_request_cool_down(self, entity):
        """
        Check if a database request is still cooling down.
        :param entity: Azure.Data.Tables.Entity
        :return: Bool
        """
        return entity['LastRun'] and \
            datetime.datetime.now() - datetime.datetime.strptime(entity['LastRun'], '%d/%m/%Y %H:%M:%S') > \
            datetime.timedelta(minutes=self.cool_down_time)

    @staticmethod
    def _update_request_timer(entity):
        """
        Update the LastRun property of a database request so the request is not ran again until the cool down time has
        passed.
        :param entity: Azure.Data.Tables.Entity
        """
        entity['LastRun'] = datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        table_client.update_entity(entity=entity)

    def _parse_desired_months(self, start_date, end_date):

        start_date_obj = datetime.datetime.strptime(start_date, "%d/%m/%Y")
        end_date_obj = datetime.datetime.strptime(end_date, "%d/%m/%Y")
        return [self.months[i - 1] for i in range(start_date_obj.month, end_date_obj.month + 1)]

    @staticmethod
    def _parse_desks(desks_str):

        return [desk.lower() for desk in desks_str.split('+')]

    @staticmethod
    def _parse_email(email_str):

        return email_str if email_str != 'none' else ''

    def _parse_database_request(self, entity):
        """
        Parse run parameters from a database continuous run request.
        :param entity: Azure.Data.Tables.Entity
        :return: run_id (str), desired_months (list of str), desks (list of str), email (str)
        """
        run_id = entity['RowKey']
        desired_months = self._parse_desired_months(start_date=entity['StartDate'], end_date=entity['EndDate'])
        desks = self._parse_desks(desks_str=entity['Desks'])
        email = self._parse_email(email_str=entity['Email'])
        return run_id, desired_months, desks, email

    def _parse_message_request(self, message):
        """
        Parse run parameters from a database continuous run request.
        :param message: Azure.Storage.Queue.Message
        :return: run_id (str), desired_months (list of str), desks (list of str), email (str)
        """
        run_id, start_date, end_date, desks, email = message.content.split(',')
        desks = self._parse_desks(desks_str=desks)
        desired_months = self._parse_desired_months(start_date=start_date, end_date=end_date)
        email = self._parse_email(email_str=email)
        return run_id, desired_months, desks, email

    def _run_from_database(self):
        """
        Find a database request (if any) and run it.
        """
        for entity in self._database_requests:
            if not self._check_request_cool_down(entity=entity):
                self._update_request_timer(entity=entity)
                run_id, desired_months, desks, email = self._parse_database_request(entity=entity)
                kwargs = {'store_results': True, 'run_id': run_id, 'desired_months': desired_months,
                          'desks': desks, 'email': email}
                thread = threading.Thread(target=self.__run_from_database, args=entity, kwargs=kwargs, daemon=True)
                self.threads.append(thread)
                thread.start()
                return

    def __run_from_database(self, entity, **kwargs):

        results = self.check_available_dates(**kwargs)
        if results:
            table_client.delete_entity(partition_key=entity['PartitionKey'], row_key=entity['RowKey'])

    def _run_from_message(self):
        """
        Find a request from message queue (if any) and run it.
        """
        message = queue_run_once_client.receive_message()
        if message:
            try:
                queue_run_once_client.delete_message(message)
                if message.content.startswith("check_desks"):
                    self.get_available_desks()
                else:
                    run_id, desired_months, desks, email = self._parse_message_request(message=message)
                    kwargs = {'store_results': True, 'run_id': run_id, 'desired_months': desired_months,
                              'desks': desks, 'email': email}
                    thread = threading.Thread(target=self.check_available_dates, kwargs=kwargs, daemon=True)
                    self.threads.append(thread)
                    thread.start()
            except Exception as e:
                logging.error("Could not execute run-once request: {}".format(e))

    @staticmethod
    def _get_desk_options(driver):
        """
        Get all available desks from site.
        :return: list of str
        """
        desk_dropdown = Select(driver.find_element(by=By.ID, value='desk'))
        return [desk.text.strip() for desk in desk_dropdown.options if desk.text]

    @staticmethod
    def _click_month_picker(driver):
        """
        Click month picker to reveal available months on website.
        """
        month_picker_element = driver.find_element(by=By.XPATH,
                                                   value="/html/body/app/div/div[2]/div/div/div/oap-appointment/div/div/oap-appointment-reservation/div/form/div[4]/available-date-picker/div/datepicker/datepicker-inner/div/daypicker/table/thead/tr[1]/th[2]/button")
        month_picker_element.click()
        time.sleep(2)

    def _check_desk_for_available_date(self, driver, desk_value, desk_dropdown, desired_months, results,
                                       click_month_picker=True):
        """
        Check a specific desk for an available month and day if any.
        :param desk_value:
        :param desk_dropdown:
        :param desired_months: list of str
        :param results: list of results found so far (list of str)
        :param click_month_picker: Selenium element of month picker widget on site
        :return: True if a month has been clicked to find a day (Bool)
        """
        desk_dropdown.select_by_visible_text(desk_value.text)
        time.sleep(2)
        logging.info('[{}] Do {}'.format(datetime.datetime.now(), desk_value.text))
        if click_month_picker:
            self._click_month_picker(driver=driver)
        if self._check_desk_for_available_months(driver=driver, desk_value=desk_value, desired_months=desired_months,
                                                 results=results):
            return True

    def _check_desk_for_available_months(self, driver, desk_value, desired_months, results):
        """
        Check which months are available for the current desk. If any desired ones available, click the first and call
        func to find a day on that month.
        :param desk_value: str
        :param results: list of results found so far (list of str)
        :return: True if any desired months available (Bool)
        """
        potential_month_buttons = driver.find_elements(by=By.CLASS_NAME, value="btn-default")
        for potential_month_button in potential_month_buttons:
            month_text = potential_month_button.text.lower()
            if month_text in desired_months:
                if not potential_month_button.is_enabled():
                    continue
                potential_month_button.click()
                time.sleep(2)
                self._check_month_for_available_date(driver=driver, desk_value=desk_value, month=month_text,
                                                     results=results)
                return True

    @staticmethod
    def _check_month_for_available_date(driver, desk_value, month, results):
        """
        A month has been selected, now check for any available days. If there are any, add the first one to results as
        we now have a desk, month and day.
        :param desk_value: str
        :param month: str
        :param results: list of results found so far (list of str)
        """
        potential_day_buttons = driver.find_elements(by=By.CLASS_NAME, value="btn-sm")
        days_already_done = []
        for potential_day_button in potential_day_buttons:
            day_text = potential_day_button.text
            if not day_text.isdigit() or day_text in days_already_done:
                continue
            days_already_done.append(day_text)
            if not potential_day_button.is_enabled():
                continue
            results.append('{} - {} {}'.format(desk_value.text, day_text, month))
            logging.info('[{}] Found result: {}'.format(datetime.datetime.now(), desk_value.text))
            break
        else:
            logging.info('[{}] No result: {}'.format(datetime.datetime.now(), desk_value.text))

    @staticmethod
    def _store_results(run_id, results):
        """
        Store results in Azure.Data.Table.
        :param run_id: str
        :param results: str
        """
        entity = {
            'PartitionKey': 'Result',
            'RowKey': run_id,
            'Result': ','.join(results)
        }
        table_client.create_entity(entity)

    @staticmethod
    def _mail_results(email, results):
        """
        Email results using GMAIL SMTP.
        :param email: str
        :param results: str
        """
        mail_content = "\n".join(results)
        message = MIMEMultipart()
        message['From'] = sender_address
        message['To'] = email
        message['Subject'] = 'IND Datum gevonden!'
        message.attach(MIMEText(mail_content, 'plain'))
        session = smtplib.SMTP('smtp.gmail.com', 587)
        session.starttls()
        session.login(sender_address, sender_pass)
        text = message.as_string()
        session.sendmail(sender_address, email, text)
        session.quit()


if __name__ == '__main__':

    date_checker = DateChecker(url='https://oap.ind.nl/oap/nl/#/doc')
    date_checker.loop()

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import Select
from selenium.webdriver.common.by import By
from flask import Flask, request
import time
import uuid
import json
import requests
import datetime
from opencensus.trace import config_integration
from opencensus.ext.flask.flask_middleware import FlaskMiddleware
from opencensus.trace.samplers import ProbabilitySampler
from opencensus.ext.azure.trace_exporter import AzureExporter
from opencensus.trace.tracer import Tracer
from opencensus.ext.azure.log_exporter import AzureLogHandler
from opencensus.ext.azure import metrics_exporter
from opencensus.stats import aggregation as aggregation_module
from opencensus.stats import measure as measure_module
from opencensus.stats import stats as stats_module
from opencensus.stats import view as view_module
from opencensus.tags import tag_map as tag_map_module
import logging
import threading
from Common import instrumentation_key

config_integration.trace_integrations(['logging', 'requests'])
tracer = Tracer(exporter=AzureExporter(connection_string=instrumentation_key), sampler=ProbabilitySampler(1.0))
FORMAT = '[%(asctime)s] [CONTROLLER] [traceId=%(traceId)s spanId=%(spanId)s] %(message)s'
logging.basicConfig(format=FORMAT)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(AzureLogHandler(connection_string=instrumentation_key))

stats = stats_module.stats
view_manager = stats.view_manager
stats_recorder = stats.stats_recorder
number_of_jobs_measure = measure_module.MeasureInt("jobs", "number of jobs", "jobs")
workers_view = view_module.View("jobs view", "number of jobs", [], number_of_jobs_measure,
                                aggregation_module.CountAggregation())
view_manager.register_view(workers_view)
mmap = stats_recorder.new_measurement_map()
tmap = tag_map_module.TagMap()
exporter = metrics_exporter.new_metrics_exporter(connection_string=instrumentation_key)
view_manager.register_exporter(exporter)


app = Flask(__name__)
middleware = FlaskMiddleware(app,exporter=AzureExporter(connection_string=instrumentation_key),
                             sampler=ProbabilitySampler(rate=1.0),)


class DateChecker:

    def __init__(self, url, controller_timeout=300):
        """
        Check available dates on website.
        :param url: URL of website to check dates for (str)
        :param controller_timeout: time of no heartbeat check from controller in seconds until shutdown (int)
        """
        self.url = url
        self.controller_timeout = controller_timeout
        self.controller = None
        self.last_heard_from_controller = None
        self.check_controller_thread = threading.Thread(target=self.check_controller_loop, daemon=True)
        self.check_controller_thread.start()

    def check_controller_loop(self):

        while True:
            if self.controller and datetime.datetime.now() - self.last_heard_from_controller > \
                    datetime.timedelta(seconds=self.controller_timeout):
                shutdown_server()
                return
            time.sleep(5)

    def register(self):

        worker_id = str(uuid.uuid4())
        with tracer.span(name='worker_register'):
            response = requests.post("http://ind-controller-ci:5002/register?worker_id={}".format(worker_id))
        if response.text.lower().startswith('ok'):
            self.controller = response.text.split(',')[1]
            self.last_heard_from_controller = datetime.datetime.now()
        else:
            raise RuntimeError("Could not register to controller")

    def check_available_dates(self, job_id, desired_months, desks):
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
        results = ",".join(results) if results else ''
        self.return_results(job_id=job_id, results=results)

    def return_results(self, job_id, results):

        with tracer.span(name='worker_return_results'):
            requests.post("http://{}/return_result?job_id={}&result={}".format(self.controller, job_id, results))

    def get_available_desks(self, job_id):

        self._get_available_desks(job_id=job_id)

    def _get_available_desks(self, job_id):

        driver = self._init_driver()
        driver.get(self.url)
        desks = self._get_desk_options(driver=driver)
        driver.quit()
        self.return_results(job_id=job_id, results=','.join(desks))

    @staticmethod
    def _init_driver():
        """
        Start a headless chrome driver.
        """
        chrome_options = webdriver.ChromeOptions()
        chrome_options.headless = True
        return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

    def run(self, **kwargs):
        """
        Find a database request (if any) and run it.
        """
        if 'check_desks' in kwargs and kwargs['check_desks'] is True:
            del kwargs['check_desks']
            thread = threading.Thread(target=self.get_available_desks, kwargs=kwargs, daemon=True)
        else:
            thread = threading.Thread(target=self.check_available_dates, kwargs=kwargs, daemon=True)
        thread.start()
        with tracer.span(name=kwargs['job_id']):
            logger.info("Started job: {}".format(kwargs))
        mmap.measure_int_put(number_of_jobs_measure, 1)
        mmap.record(tmap)

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
        logger.info('[{}] Do {}'.format(datetime.datetime.now(), desk_value.text))
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
            logger.info('[{}] Found result: {}'.format(datetime.datetime.now(), desk_value.text))
            break
        else:
            logger.info('[{}] No result: {}'.format(datetime.datetime.now(), desk_value.text))


def shutdown_server():
    logger.info("Shutting down server")
    func = request.environ.get('werkzeug.server.shutdown')
    func()
    quit()


@app.route("/start_job", methods=['POST'])
def start_job():

    kwargs = json.loads(request.json)
    date_checker.run(**kwargs)
    return "OK"


@app.route("/adopt", methods=['POST'])
def adopt():

    date_checker.last_heard_from_controller = datetime.datetime.now()
    date_checker.controller = request.remote_addr + ":5002"
    return "OK"


@app.route("/heartbeat", methods=['GET'])
def heartbeat():

    date_checker.last_heard_from_controller = datetime.datetime.now()
    return "OK"


if __name__ == '__main__':

    date_checker = DateChecker(url='https://oap.ind.nl/oap/nl/#/doc')
    timeout = 0
    while True:
        try:
            date_checker.register()
        except Exception as e:
            logger.error("Could not register to controller: {}".format(e))
            timeout += 1
            if timeout >= 120:
                quit()
            time.sleep(1)
        else:
            break
    app.run(host='0.0.0.0', port=5003)

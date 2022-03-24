import azure.core.exceptions
from flask import Flask, request
from azure.storage.queue import QueueClient
from azure.data.tables import TableServiceClient
from Common import connect_str, table_name, queue_name, sender_address, sender_pass
import collections
import threading
import datetime
import requests
import logging
import smtplib
import json
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)


queue_run_once_client = QueueClient.from_connection_string(connect_str, queue_name)
table_service = TableServiceClient.from_connection_string(conn_str=connect_str)
table_client = table_service.get_table_client(table_name=table_name)


class RegisteredWorker(object):

    def __init__(self, worker_id=None, remote_addr=None, new_worker=False, sync_from=None):
        """
        Object representing a worker that retrieves available dates. Workers registered to a controller are
        assigned jobs by it. Controllers also create RegisteredWorker objects to represent workers registered to
        different controllers. These are used to check job across workers of all controllers, to ensure an even
        distribution. Use new_worker=True when a new worker registers to a controller, and new_worker=False with
        a Table entity in sync_from representing the worker to represent workers of different controllers.
        Workers contain a list of active jobs, so the controller can check if they return in a timely manner, and
        restart them if not.
        :param worker_id: uuid4 (str)
        :param remote_addr: ip_addr:port (str)
        :param new_worker: True if adding a new active worker to pool, False if just syncing (Bool)
        :param sync_from: Sync a worker from a database entity (TableClient.entity)
        """
        self._entity = None
        self.jobs = collections.OrderedDict()
        self.last_job_started_at = None
        if new_worker:
            self.worker_id = worker_id
            self.register_in_database(worker_id=worker_id, remote_addr=remote_addr)
        else:
            self.worker_id = sync_from['RowKey']
            self._entity = sync_from

    @property
    def ready(self):
        """
        Returns whether this worker is ready to accept a new job. Takes into account even distribution of jobs across
        all workers, job assignment cool down and max number of jobs.
        :return: Bool
        """
        if len(self.jobs) != controller.lowest_amount_of_jobs:
            return False
        elif len(self.jobs) > controller.max_number_of_jobs:
            return False
        elif not self.last_job_started_at:
            return True
        return datetime.datetime.now() - self.last_job_started_at > datetime.timedelta(
            seconds=controller.worker_cooldown)

    @property
    def entity(self):
        """
        Return table entity associated with this worker.
        :return: Azure.Data.Table.entity
        """
        if not self._entity:
            self._entity = table_client.get_entity(partition_key='RegisteredWorkers', row_key=self.worker_id)
        return self._entity

    @property
    def remote_addr(self):
        """
        :return: ip_addr:port (str)
        """
        return self.entity['remote_addr']

    @property
    def last_heartbeat(self):

        return self.entity['last_heartbeat']

    def register_in_database(self, worker_id, remote_addr):
        """
        Create a table entity representing this worker.
        :param worker_id: uuid4 (str)
        :param remote_addr: ip_addr:port (str)
        """
        entity = {
            'PartitionKey': 'RegisteredWorkers',
            'RowKey': worker_id,
            'remote_addr': remote_addr,
            'last_heartbeat': datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        }
        table_client.create_entity(entity)

    def unregister_from_database(self):
        """
        Delete the table entity representing this worker.
        """
        table_client.delete_entity(partition_key='RegisteredWorkers', row_key=self.worker_id)

    def start_job(self, job_type, email=None, **kwargs):
        """
        Start a job on this worker and create a RegisteredJob object to allow the controller to keep track of it.
        :param job_type: run-once / continuous / get-desks (str)
        :param email: email address to mail results to if any (str)
        :param kwargs: parameters to send to worker for the job (dict)
        """
        post_json = json.dumps(kwargs)
        self.last_job_started_at = datetime.datetime.now()
        response = requests.post("http://{}:5003/start_job".format(self.remote_addr), json=post_json)
        assert response.text.lower() == 'ok'
        self.jobs[kwargs['job_id']] = RegisteredJob(job_id=kwargs['job_id'], job_type=job_type, assigned_worker=self,
                                                    email=email, args=post_json)

    def shutdown(self):
        """
        Shutdown the worker container represented by this object.
        """
        try:
            requests.post("http://{}:5003/shutdown".format(self.remote_addr))
        except Exception as e:
            logging.error("Could not shutdown '{}@{}' with: {}. perhaps it's already down?".format(
                self.worker_id, self.remote_addr, e))
        self.unregister_from_database()


class RegisteredJob(object):

    def __init__(self, job_id, job_type, args, assigned_worker=None, email=None):
        """
        Object representing a job running on a worker. Used by controller to keep track of jobs so it can restart them
        if they don't return, and handle results if they do return.
        :param job_id: uuid4 (str)
        :param job_type: run-once / continuous / get-desks (str)
        :param args: arguments used to start the job on the worker (dict)
        :param assigned_worker: uuid4 or worker (str)
        :param email: email address to mail the results to if any (str)
        """
        self._entity = None
        self.job_id = job_id
        self.register_in_database(job_id=job_id, job_type=job_type, assigned_worker=assigned_worker.worker_id,
                                  email=email, args=args)

    @property
    def entity(self):
        """
        Return table entity associated with this job.
        :return: Azure.Data.Table.entity
        """
        if not self._entity:
            self._entity = table_client.get_entity(partition_key='RegisteredJobs', row_key=self.job_id)
        return self._entity

    @property
    def assigned_worker(self):
        """
        :return: uuid4 of worker (str)
        """
        return self.entity['assigned_worker']

    @property
    def type(self):
        """
        :return: run-once / continuous / get-desks (str)
        """
        return self.entity['type']

    @property
    def email(self):
        """
        :return: email address to mail the results to if any (str)
        """
        return self.entity['email']

    @property
    def started(self):
        """
        :return: time when this job was started ('%d/%m/%Y %H:%M:%S' datatime str)
        """
        return self.entity['started']

    @property
    def args(self):
        """
        :return: arguments used to start the job on the worker (dict)
        """
        return self.entity['args']

    def register_in_database(self, job_id, job_type, assigned_worker, args, email=None):
        """
        Create a table entity representing this job.
        :param job_id: uuid4 (str)
        :param job_type: run-once / continuous / get-desks (str)
        :param args: arguments used to start the job on the worker (dict)
        :param assigned_worker: uuid4 or worker (str)
        :param email: email address to mail the results to if any (str)
        """
        entity = {
            'PartitionKey': 'RegisteredJobs',
            'RowKey': job_id,
            'type': job_type,
            'assigned_worker': assigned_worker,
            'email': email or '',
            'args': args,
            'started': datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        }
        table_client.create_entity(entity)

    def unregister_from_database(self):
        """
        Delete the table entity representing this job.
        """
        table_client.delete_entity(partition_key='RegisteredJobs', row_key=self.job_id)

    def complete(self, results):
        """
        Called when results are returned by a worker. Handle it by storing results in the azure.data.table and emailing
        them if an email is set for this job.
        :param results: str
        """
        job_type = self.type
        self.unregister_from_database()
        if job_type == 'run-once':
            self._complete_run_once_job(results=results)
        elif job_type == 'get-desks':
            self._complete_get_desks_job(results=results)
        elif job_type == 'continuous':
            self._complete_continuous_job(results=results)

    def _complete_run_once_job(self, results):
        """
        :param results: str
        """
        controller.store_results(job=self, results=results)

    def _complete_continuous_job(self, results):
        """
        :param results: str
        """
        entity = table_client.get_entity(partition_key='ContinuousRun', row_key=self.job_id)
        if not results:
            entity['ErrorCount'] = 0
            table_client.update_entity(entity)
            return
        controller.store_results(job=self, results=results)
        controller.mail_results(job=self, results=results)
        table_client.delete_entity(entity)

    @staticmethod
    def _complete_get_desks_job(results):
        """
        :param results: str
        """
        entity = {
            'PartitionKey': 'Desks',
            'RowKey': '0',
            'Desks': results,
            'CheckedAt': datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        }
        table_client.create_entity(entity)


class Controller(object):

    def __init__(self, cool_down_time=5, heartbeat_time=30, worker_timeout=90, job_timeout=300, max_number_of_jobs=20,
                 worker_cooldown=3, max_job_errors=3, max_worker_errors=3):
        """
        Objects representing a controller that workers can register to. Controllers assign jobs to workers that are
        registered to them (specifically). Controllers are also aware of workers registered to other controllers,
        in order to distribute load evenly. Jobs that were assigned are kept track of so they can be restarted if no
        results return before the timeout period. Jobs that error out too many times are cancelled, and workers that
        have an error or fail to return results too many times are shut down.
        :param cool_down_time: how often to check a continuous run request in minutes (int)
        :param heartbeat_time: time in seconds between hearbeat checks to registered workers (int)
        :param worker_cooldown: time in seconds of no heartbeat until worker registration is removed (int)
        :param job_timeout: time in seconds of not hearing back from an assigned job before stopping it (int)
        :param max_number_of_jobs: maximum number of jobs to assign to a single worker simultaneously (int)
        :param worker_timeout: minimum time in seconds to wait before assigning another job to a worker (int)
        :param max_job_errors: maximum amount of times a job may fail in a row until it's deleted permanently (int)
        :param max_job_errors: maximum amount of times a worker may fail in total until it's shutdown (int)
        """
        self.registered_workers = collections.deque()
        self.synced_workers = collections.deque()
        self.cool_down_time = cool_down_time
        self.heartbeat_time = heartbeat_time
        self.worker_timeout = worker_timeout
        self.job_timeout = job_timeout
        self.max_number_of_jobs = max_number_of_jobs
        self.worker_cooldown = worker_cooldown
        self.max_job_errors = max_job_errors
        self.max_worker_errors = max_worker_errors
        self.jobs_to_restart = collections.deque()
        self.months = ['januari', 'februari', 'maart', 'april', 'mei', 'juni', 'juli', 'augustus', 'september',
                       'oktober', 'november', 'december']
        self.heartbeat_thread = threading.Thread(target=self.check_heartbeats_loop, daemon=True)
        self.heartbeat_thread.start()
        self.distribute_jobs_thread = threading.Thread(target=self.distribute_jobs_loop, daemon=True)
        self.distribute_jobs_thread.start()
        self.sync_workers_thread = threading.Thread(target=self.sync_workers_loop, daemon=True)
        self.sync_workers_thread.start()
        self.check_jobs_thread = threading.Thread(target=self.check_jobs_loop, daemon=True)
        self.check_jobs_thread.start()

    def register_worker(self, worker_id=None, remote_addr=None, worker_obj=None):
        """
        Register a worker to this controller.
        :param worker_id: uuid4 (str)
        :param remote_addr: ip_addr:port (str)
        :param worker_obj: pre existing RegisteredWorker object (RegisteredWorker)
        """
        worker_obj = worker_obj or RegisteredWorker(new_worker=True, worker_id=worker_id, remote_addr=remote_addr)
        self.registered_workers.append(worker_obj)
        logging.info("Registered: {}@{}".format(worker_id, remote_addr))

    def unregister_worker(self, worker):
        """
        Deregister a worker to this controller.
        :param worker: RegisteredWorker object
        """
        worker.unregister_database()
        self.registered_workers.remove(worker)

    def adopt_worker(self, worker):
        """
        Adopt an orphaned worker, e.g. for when original controller died.
        :param worker: RegisteredWorker
        """
        try:
            response = requests.post("http://{}/adopt".format(worker.remote_addr))
            assert response.text.lower() == 'ok'
        except Exception as e:
            logging.warning("Cannot adopt '{}@{}': {}. Unregisting it completely.".format(
                worker.worker_id, worker.remote_addr, e))
            worker.unregister_from_database()
        else:
            self.register_worker(worker_obj=worker)

    @property
    def lowest_amount_of_jobs(self):
        """
        Returns the lowest amount of jobs held by any worker across all controllers. By always assigning jobs to the
        worker(s) with the lowest amount of jobs, load is distributed evenly.
        :return: int
        """
        return max([len(worker.jobs) for worker in self.registered_workers])

    def handle_result(self, job_id, results):
        """
        :param job_id: uuid4 (str)
        :param results: str
        """
        for worker in self.registered_workers:
            if job_id in worker.jobs.keys():
                worker.jobs[job_id].complete(results=results)

    @staticmethod
    def store_results(job, results):
        """
        Store results in Azure.Data.Table.
        :param job: str
        :param results: str
        """
        entity = {
            'PartitionKey': 'Result',
            'RowKey': job.job_id,
            'Result': results
        }
        table_client.create_entity(entity)

    @staticmethod
    def mail_results(job, results):
        """
        Email results using GMAIL SMTP.
        :param job: str
        :param results: str
        """
        mail_content = "\n".join(results.split(','))
        message = MIMEMultipart()
        message['From'] = sender_address
        message['To'] = job.email
        message['Subject'] = 'IND Datum gevonden!'
        message.attach(MIMEText(mail_content, 'plain'))
        session = smtplib.SMTP('smtp.gmail.com', 587)
        session.starttls()
        session.login(sender_address, sender_pass)
        text = message.as_string()
        session.sendmail(sender_address, job.email, text)
        session.quit()

    def check_heartbeats_loop(self):
        """
        Loop that keeps track of worker heartbeat, so workers can be unregistered if unresponsive.
        """
        while True:
            for worker in self.registered_workers.copy():
                self._make_heartbeat(worker=worker)
            for worker in self.synced_workers:
                self._check_possible_orphaned_synced_worker(worker=worker)
            time.sleep(10)

    def sync_workers_loop(self):
        """
        Keep aware of workers registered to other controllers. Jobs are not assigned to them, they are just used to
        distribute load evenly.
        """
        for synced_worker in self.synced_workers.copy():
            try:
                table_client.get_entity(partition_key='RegisteredWorkers', row_key=synced_worker.worker_id)
            except azure.core.exceptions.ResourceNotFoundError:
                self.synced_workers.remove(synced_worker)
        for entity in table_client.query_entities("PartitionKey eq 'RegisteredWorkers'"):
            if entity['RowKey'] not in [worker.worker_id for worker in self.registered_workers + self.synced_workers]:
                worker = RegisteredWorker(sync_from=entity)
                if datetime.datetime.now() - datetime.datetime.strptime(worker.last_heartbeat, '%d/%m/%Y %H:%M:%S') \
                      > datetime.timedelta(seconds=self.worker_timeout + self.heartbeat_time):
                    self._handle_orphaned_worker(worker=worker)

                else:
                    self.synced_workers.append(RegisteredWorker(sync_from=entity))

    def distribute_jobs_loop(self, from_queue=True, from_database=True):
        """
        Find a job to do for a worker (if any). Can be a job thats needs restarting after timing/erroring out, a job
        from the message queue (run once/get desks) or a job from the Table (continuous). If a job is found, try to
        find an available worker. If there's a job and an available worker, assign the job.
        :param from_queue: check run once requests from message queue (Bool)
        :param from_database: check continuous run requests from database (Bool)
        """
        message_switch = True
        while True:
            worker = self._available_worker
            if worker:
                logging.info("Looking for work for: {}".format(worker.worker_id))
                if self.jobs_to_restart:
                    job = self.jobs_to_restart.popleft()
                    self._restart_job(worker=worker, job=job)
                elif message_switch and from_queue:
                    self._get_job_from_message(worker=worker)
                elif not message_switch and from_database:
                    self._get_job_from_database(worker=worker)
                message_switch = not message_switch
            time.sleep(1)

    def check_jobs_loop(self):
        """
        Check if any jobs assigned by this controller are timed out. If so restart them if they have not yet reached
        max error count, or delete them if so. Check jobs not assigned to this controller, if they are lingering for
        double the timeout time, restart them on this controller (assuming the other controller died, since it would
        have picked up this job otherwise).
        """
        while True:
            self._check_own_jobs()
            self._check_foreign_jobs()
            time.sleep(10)

    def _check_own_jobs(self):

        for worker in self.registered_workers:
            for job in worker.jobs.copy():
                if datetime.datetime.now() - datetime.datetime.strptime(job.started, '%d/%m/%Y %H:%M:%S') > \
                        datetime.timedelta(seconds=self.job_timeout):
                    worker.jobs.remove(job)
                    worker.errors += 1
                    if worker.errors >= self.max_worker_errors:
                        worker.shutdown()
                    self.jobs_to_restart.append(job)

    def _check_foreign_jobs(self):

        for job_entity in table_client.query_entities("PartitionKey eq 'RegisteredJobs'"):
            if datetime.datetime.now() - datetime.datetime.strptime(job_entity['started'], '%d/%m/%Y %H:%M:%S') > \
                    datetime.timedelta(seconds=self.job_timeout * 2):
                job = RegisteredJob(job_id=job_entity['job_id'], job_type=job_entity['type'], args=job_entity['args'],
                                    email=job_entity['email'])
                self.jobs_to_restart.append(job)

    def _make_heartbeat(self, worker):
        """
        Make heartbeat, handle possible timeouts.
        :param worker: RegisteredWorker object
        """
        last_heartbeat_obj = datetime.datetime.strptime(worker.last_heartbeat, '%d/%m/%Y %H:%M:%S')
        if datetime.datetime.now() - last_heartbeat_obj > datetime.timedelta(seconds=self.heartbeat_time):
            try:
                reponse = requests.get("http://{}:5003/heartbeat".format(worker.remote_addr), timeout=3)
                if reponse.text == 'OK':
                    worker.entity['last_heartbeat'] = datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')
                    table_client.update_entity(worker.entity)
                    return True
            except Exception as e:
                logging.error("Heartbeat failed for {}@{}: {}".format(worker.worker_id, worker.remote_addr, e))
                if datetime.datetime.now() - last_heartbeat_obj > datetime.timedelta(seconds=self.worker_timeout):
                    self.unregister_worker(worker=worker)

    def _check_possible_orphaned_synced_worker(self, worker):
        """
        If the worker_timeout for a worker has been reached, and another heartbeat_time has passed (i.e. its' controller
        would have had time to unregister it), assume the workers' controller has died and adopt it.
        :param worker: RegisteredWorker object
        """
        last_heartbeat_obj = datetime.datetime.strptime(worker.last_heartbeat, '%d/%m/%Y %H:%M:%S')
        if datetime.datetime.now() - last_heartbeat_obj > datetime.timedelta(
                seconds=self.heartbeat_time + self.worker_timeout):
            self._handle_orphaned_worker(worker=worker)

    def _handle_orphaned_worker(self, worker):
        """
        Try to adopt a worker. If not possible, unregister it from database altogether.
        :param worker: RegisteredWorker object
        """
        try:
            if self._make_heartbeat(worker=worker):
                logging.warning("Adopting orphaned worker: {}@{}".format(worker.worker_id,
                                                                         worker.remote_addr))
                self.adopt_worker(worker=worker)
        except Exception as e:
            logging.warning("Orphaned worker not responding, deleting from database: {}@{} ({})".format(
                worker.worker_id, worker.remote_addr, e))
            worker.unregister_from_database()
            if worker in self.synced_workers:
                self.synced_workers.remove(worker)

    @staticmethod
    def _restart_job(job, worker):
        """
        Restart a job. Only happens when it previously failed or timed out.
        :param job: RegisteredJob object
        :param worker: RegisteredWorker object
        """
        job.unregister_from_database()
        worker.start_job(job_type=job.type, email=job.email, kwargs=job.args)

    @property
    def _available_worker(self):
        """
        Returns the first available worker found.
        :return: RegisteredWorker object
        """
        for worker in self.registered_workers:
            if worker.ready:
                return worker

    def _get_job_from_database(self, worker):
        """
        Find a database request (if any) and run it.
        """
        for entity in self._database_requests:
            if not self._check_request_cool_down(entity=entity):
                error_count = None
                original_last_run_time = entity['LastRun']
                try:
                    self._update_request_timer(entity=entity)
                    job_id, desired_months, desks, email, error_count = self._parse_database_request(entity=entity)
                    kwargs = {'job_id': job_id, 'desired_months': desired_months, 'desks': desks}
                    worker.start_job(job_type='continuous', **kwargs)
                except Exception as e:
                    logging.error("Could not start job on worker '{}': {}".format(worker.worker_id, e))
                    if error_count:
                        error_count += 1
                        if error_count < self.max_job_errors:
                            self._update_request_timer(entity=entity, date_time=original_last_run_time)
                return

    def _get_job_from_message(self, worker):
        """
        Find a request from message queue (if any) and run it.
        """
        message = queue_run_once_client.receive_message()
        if message:
            queue_run_once_client.delete_message(message)
            error_count = None
            try:
                if message.content.startswith("check_desks"):
                    error_count = message.content.split(',')[-1]
                    worker.start_job(job_type='check-desks', check_desks=True)
                else:
                    job_id, desired_months, desks, error_count = self._parse_message_request(message=message)
                    kwargs = {'job_id': job_id, 'desired_months': desired_months, 'desks': desks}
                    worker.start_job(job_type='run-once', **kwargs)
            except Exception as e:
                logging.error("Could not start job on worker '{}': {}".format(worker.worker_id, e))
                if error_count:
                    error_count += 1
                    if error_count < self.max_job_errors:
                        queue_run_once_client.send_message(message)

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
    def _update_request_timer(entity, date_time=None):
        """
        Update the LastRun property of a database request so the request is not ran again until the cool down time has
        passed.
        :param entity: Azure.Data.Tables.Entity
        :param date_time: time to update with (optional, None for current time)
        """
        entity['LastRun'] = date_time or datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        table_client.update_entity(entity=entity)

    def _parse_desired_months(self, start_date, end_date):
        """
        From a start and end date, find the months in which to search for an available date.
        :param start_date: datetime
        :param end_date: datetime
        :return:
        """
        start_date_obj = datetime.datetime.strptime(start_date, "%d/%m/%Y")
        end_date_obj = datetime.datetime.strptime(end_date, "%d/%m/%Y")
        return [self.months[i - 1] for i in range(start_date_obj.month, end_date_obj.month + 1)]

    @staticmethod
    def _parse_desks(desks_str):
        """
        Return a list of desks from a desks string.
        :param desks_str: str
        :return: list of str
        """
        return [desk.lower() for desk in desks_str.split('+')]

    @staticmethod
    def _parse_email(email_str):
        """
        :param email_str: str
        :return: str
        """
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
        error_count = entity['ErrorCount']
        return run_id, desired_months, desks, email, error_count

    def _parse_message_request(self, message):
        """
        Parse run parameters from a database continuous run request.
        :param message: Azure.Storage.Queue.Message
        :return: run_id (str), desired_months (list of str), desks (list of str), email (str)
        """
        run_id, start_date, end_date, desks, error_count = message.content.split(',')
        desks = self._parse_desks(desks_str=desks)
        desired_months = self._parse_desired_months(start_date=start_date, end_date=end_date)
        return run_id, desired_months, desks, int(error_count)

    @property
    def _database_requests(self):
        """
        :return: List of azure.data.table.entity
        """
        my_filter = "PartitionKey eq 'ContinuousRun'"
        return table_client.query_entities(my_filter)


def shutdown_server():
    func = request.environ.get('werkzeug.server.shutdown')
    func()
    for worker in controller.registered_workers:
        worker.shutdown()


@app.route("/register", methods=['POST'])
def register():

    worker_id = request.args['worker_id']
    controller.register_worker(worker_id=worker_id, remote_addr=request.remote_addr)
    return "OK,{}".format(request.host)


@app.route("/return_result", methods=['POST'])
def return_result():

    job_id = request.args['job_id']
    results = request.args['result']
    controller.handle_result(job_id=job_id, results=results)
    return "OK"


if __name__ == '__main__':
    controller = Controller()
    app.run(host='0.0.0.0', port=5002)

from flask import Flask, render_template, url_for, flash, request, redirect
import datetime
from dateutil.relativedelta import relativedelta
import requests
import json
from Common import secret_key

api_server = '127.0.0.1:5001'

app = Flask(__name__)
app.secret_key = secret_key


def get_desks():

    # Get from API
    return ['IND Den Bosch', 'IND Amsterdam', 'IND Utrecht', 'IND Groningen', 'IND Zwolle']


def request_run_once(parameters):
    return requests.post("http://{}/run_once".format(api_server), json=json.dumps(parameters))


def request_run_continuous(parameters):
    return requests.post("http://{}/run_continuous".format(api_server), json=json.dumps(parameters))


@app.route("/", methods=('GET', 'POST'))
def root():
    if request.method == 'POST':
        daterange = request.form['daterange']
        desks = [desk.split('desk_')[1] for desk, value in request.form.items()
                 if desk.startswith('desk_') and value == 'on']
        method = request.form['method']
        email = request.form['email']
        start_date = daterange.split(' - ')[0]
        end_date = daterange.split(' - ')[1]
        if method == 'run_continuously' and not email:
            flash('Email nodig bij constant zoeken.')
        elif not desks:
            flash('Minstens één desk benodigd om een datum te zoeken.')
        elif datetime.datetime.strptime(start_date, '%d/%m/%Y') < datetime.datetime.today() - relativedelta(days=1):
            flash('Start datum mag niet in het verleden liggen.')
        else:
            try:
                if method == 'run_once':
                    response = request_run_once(parameters={'start_date': start_date, 'end_date': end_date, 'email': email,
                                                            'desks': desks})
                else:
                    response = request_run_continuous(parameters={'start_date': start_date, 'end_date': end_date,
                                                                  'email': email, 'desks': desks})
                return render_template('/result.html', run_id=response.text, continuous=method != 'run_once',
                                       email=email)
            except Exception as e:
                flash('No connection to API server: {}'.format(e))
    now = datetime.datetime.now()
    start_date = "{}/{}/{}".format(now.day, now.month, now.year)
    three_months_later = datetime.datetime.now() + relativedelta(months=3)
    end_date = "{}/{}/{}".format(three_months_later.day, three_months_later.month, three_months_later.year)
    return render_template('index.html', desks=get_desks(), start_date=start_date, end_date=end_date)


@app.route("/index", methods=('GET', 'POST'))
def index():
    return root()


@app.route("/get_result")
def get_result():

    run_id = request.args['run_id']
    response = requests.get("http://{}/get_result?run_id={}".format(api_server, run_id))
    if response.text:
        return "Results found: <br>" + response.text.replace(',', '<br>')
    else:
        return "Still waiting.."


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

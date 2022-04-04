FROM selenium/standalone-chrome

RUN sudo apt-get update && sudo apt-get -y install python3 python3-pip
RUN sudo mkdir -p /worker
RUN pip3 install selenium flask webdriver_manager opencensus-ext-azure opencensus-ext-logging opencensus-ext-flask opencensus-ext-requests
RUN pip3 install markupsafe==2.0.1

EXPOSE 5003 5003

COPY Worker.py /worker/Worker.py
COPY Common.py /worker/Common.py

WORKDIR /worker
ENTRYPOINT ["python3", "/worker/Worker.py"]

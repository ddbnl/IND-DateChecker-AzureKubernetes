FROM ubuntu:latest

RUN apt-get update && apt-get -y install python3 python3-pip
RUN mkdir -p /web_frontend
RUN pip3 install flask requests python-dateutil opencensus-ext-azure opencensus-ext-logging opencensus-ext-flask opencensus-ext-requests
RUN pip3 install markupsafe==2.0.1

EXPOSE 5000 5000

COPY Common.py /web_frontend/Common.py
COPY WebFrontEnd.py /web_frontend/WebFrontEnd.py
COPY templates /web_frontend/templates

WORKDIR /web_frontend
ENTRYPOINT ["python3", "/web_frontend/WebFrontEnd.py"]

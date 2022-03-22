FROM ubuntu:latest

RUN apt-get update && apt-get -y install python3 python3-pip
RUN mkdir -p /web_frontend
RUN pip3 install flask requests python-dateutil

EXPOSE 5000 5000

COPY WebFrontEnd.py /web_frontend/WebFrontEnd.py
COPY templates /web_frontend/templates

WORKDIR /web_frontend
ENTRYPOINT ["python3", "/web_frontend/WebFrontEnd.py"]

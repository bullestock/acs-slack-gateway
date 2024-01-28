FROM python:3.10-bullseye

ARG GIT_COMMIT=unknown
LABEL git-commit=$GIT_COMMIT

COPY ./service.py /opt/service/
COPY ./requirements.txt /opt/service/
WORKDIR /opt/service

RUN pip install -r requirements.txt
RUN mkdir /opt/service/monitoring

EXPOSE 5000

ENTRYPOINT ["python3"]
CMD ["service.py"]
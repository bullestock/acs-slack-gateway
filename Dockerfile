FROM python:3.14-bookworm

ARG GIT_COMMIT=unknown
LABEL git-commit=$GIT_COMMIT

COPY ./service.py /opt/service/
COPY ./pyproject.toml /opt/service/
WORKDIR /opt/service

RUN mkdir /opt/service/monitoring
RUN uv sync && . ./venv/bin/activate

EXPOSE 5000

ENTRYPOINT ["python3"]
CMD ["service.py"]
FROM apache/airflow:2.8.0-python3.11

USER root
RUN apt-get update && apt-get install -y openjdk-17-jdk && apt-get clean

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

USER airflow
RUN pip install --no-cache-dir apache-airflow==2.8.0 \
    && pip install --no-cache-dir pyspark==3.5.1 \
    && pip install --no-cache-dir grpcio-status==1.59.0 \
    && pip install --no-cache-dir apache-airflow-providers-apache-spark==4.11.0 \
    && pip install --no-cache-dir minio \
    && pip install --no-cache-dir pydeequ \
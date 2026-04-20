
#spark — Image chính thức Apache Spark 3.5.5 trên Ubuntu 22.04 (Jammy) + Python 3.11 như Airflow.

FROM apache/spark:3.5.5-scala2.12-java17-python3-ubuntu AS spark

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.11 python3.11-venv \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 100 \
    && update-alternatives --set python3 /usr/bin/python3.11 \
    && rm -rf /var/lib/apt/lists/*

ENV SPARK_HOME=/opt/spark
RUN mkdir -p "${SPARK_HOME}/conf" \
    && printf '%s\n' \
        'spark.pyspark.python /usr/bin/python3.11' \
        'spark.executorEnv.PYSPARK_PYTHON /usr/bin/python3.11' \
        >> "${SPARK_HOME}/conf/spark-defaults.conf"

ENV PYSPARK_PYTHON=/usr/bin/python3.11
ENV PYSPARK_DRIVER_PYTHON=/usr/bin/python3.11

USER spark

# -----------------------------------------------------------------------------
# airflow — stage mặc định (cuối file).
# -----------------------------------------------------------------------------
FROM apache/airflow:2.8.0-python3.11 AS airflow

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jdk python3.11-venv \
    && rm -rf /var/lib/apt/lists/*

ENV PYSPARK_PYTHON=/usr/bin/python3.11
ENV PYSPARK_DRIVER_PYTHON=/usr/bin/python3.11
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

USER airflow
# pyspark phải cài sau cùng và ép version: pydeequ/provider có thể kéo PySpark khác → lệch với cluster 3.5.5
# gây InvalidClassException (org.apache.spark.scheduler.Task serialVersionUID) giữa driver và executor.
RUN pip install --no-cache-dir apache-airflow==2.8.0 \
    && pip install --no-cache-dir grpcio-status==1.59.0 \
    && pip install --no-cache-dir apache-airflow-providers-apache-spark==4.11.0 \
    && pip install --no-cache-dir minio \
    && pip install --no-cache-dir pydeequ \
    && pip install --no-cache-dir --force-reinstall "pyspark==3.5.5" \
    && python -c "import pyspark; assert pyspark.__version__ == '3.5.5', pyspark.__version__"

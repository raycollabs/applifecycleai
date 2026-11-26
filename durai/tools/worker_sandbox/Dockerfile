FROM durai-spike-sandbox:2
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir --break-system-packages deepagents langchain-openai "psycopg[binary]"
COPY durai /opt/durai/durai
ENV PYTHONPATH=/opt/durai
USER ubuntu

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py aas_access.py capability_matching.py config_models.py \
     lifecycle.py mqtt_transport.py orchestration.py ./

CMD ["python", "agent.py"]

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py aas_access.py aas_reference.py catalog_runtime.py \
     config_models.py mqtt_transport.py orchestration.py registry_client.py \
     research_metrics.py reservation.py semantic_catalog.py \
     semantic_model.py semantic_parser.py semantics.py ./

CMD ["python", "agent.py"]

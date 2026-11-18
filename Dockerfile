FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /site
COPY requirements.txt requirements-tools.txt ./
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt -r requirements-tools.txt

# The project is bind-mounted at /site by compose.yaml
CMD ["python", "build.py"]

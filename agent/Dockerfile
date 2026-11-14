# syntax=docker/dockerfile:1
# The agent image AgentCore Runtime runs (build step 6). arm64 is required by
# AgentCore. Build context: the repo root (the image needs agent/ and
# data/area_stats.json; CDK's asset `exclude` list keeps everything else out).
#
#   docker buildx build --platform linux/arm64 -f agent/Dockerfile -t dubainest-agent .
#   docker run --rm -p 8080:8080 -v ~/.aws:/home/agent/.aws:ro -e AWS_REGION=us-east-1 dubainest-agent
#   curl -sN localhost:8080/invocations -H 'content-type: application/json' -d '{"prompt":"hi"}'
#
# Listings come from DynamoDB (one Scan per container); the vector index from
# INDEX_URI (S3), both set by CDK.

ARG PYTHON_VERSION=3.12

FROM public.ecr.aws/docker/library/python:${PYTHON_VERSION}-slim AS build
WORKDIR /build
COPY agent/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM public.ecr.aws/docker/library/python:${PYTHON_VERSION}-slim
COPY --from=build /install /usr/local
WORKDIR /app
COPY agent/ agent/
COPY data/area_stats.json data/area_stats.json
RUN rm -rf agent/tests && useradd --uid 10001 --no-create-home agent
USER agent

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "agent.main"]

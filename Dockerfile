FROM python:3.11-slim@sha256:0b23cfb7425d065008b778022a17b1551c82f8b4866ee5a7a200084b7e2eafbf AS build

WORKDIR /src

RUN pip install --upgrade pip
RUN pip install --no-cache-dir poetry==2.3.2 poetry-plugin-export==1.9.0
COPY pyproject.toml poetry.lock README.md /src/
COPY diff_poetry_lock /src/diff_poetry_lock
RUN poetry export --format=requirements.txt --output=requirements.txt --without-hashes --only=main
RUN poetry build --format=wheel

FROM python:3.11-slim@sha256:0b23cfb7425d065008b778022a17b1551c82f8b4866ee5a7a200084b7e2eafbf

WORKDIR /src

COPY --from=build /src/requirements.txt /tmp/requirements.txt
COPY --from=build /src/dist/*.whl /tmp/
RUN pip install --no-cache-dir --requirement /tmp/requirements.txt \
	&& pip install --no-cache-dir /tmp/*.whl \
	&& rm -f /tmp/requirements.txt /tmp/*.whl

COPY entrypoint.sh /src/entrypoint.sh

ENTRYPOINT ["/src/entrypoint.sh"]

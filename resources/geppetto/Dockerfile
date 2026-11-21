FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap ripgrep ca-certificates && rm -rf /var/lib/apt/lists/*
COPY *.whl /tmp/wheels/
RUN --mount=type=cache,target=/root/.cache/pip pip install /tmp/wheels/*.whl openai-codex==0.171.0 && rm -rf /tmp/wheels && \
    ln -s "$(python -c 'from importlib.util import find_spec; from pathlib import Path; print(Path(find_spec("codex_cli_bin").origin).parent / "bin" / "codex")')" /usr/local/bin/codex && \
    mkdir -p /home/geppetto/.agents/skills /work /input /var/lib/geppetto && \
    cp -R /usr/local/share/diorama/geppetto/skills/. /home/geppetto/.agents/skills/ && \
    chmod 1777 /var/lib/geppetto
ENV HOME=/home/geppetto CODEX_HOME=/var/lib/geppetto
WORKDIR /work
ENTRYPOINT ["codex"]

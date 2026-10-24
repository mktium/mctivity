FROM public.ecr.aws/docker/library/debian:13-slim@sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad
RUN apt-get update && apt-get install -y --no-install-recommends \
    e2fsprogs python3 gdisk dosfstools \
    && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["/bin/sh"]

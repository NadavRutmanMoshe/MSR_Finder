# MSR_Finder in a container: a clean Ubuntu 26.04 (the TACAS 2027 VM's release),
# set up by the same install.sh as the VM, and checked by the same smoke test.
#
#     docker build -t msr-finder .                         (~15 min on amd64, longer on arm64)
#     docker run --rm -it msr-finder                       a shell, environment loaded
#     docker run --rm msr-finder ./smoke_test.sh           the smoke test again
#
# An image that finished building passed the smoke test: the build runs it last.
# Build args:  INSTALL_ARGS="--tool-only"  to leave out the three baselines.
FROM ubuntu:26.04@sha256:c098726dd9b9733f3e885f8de0ef375206a311b0a317eadc40d347b1115fb944

# What the TACAS VM has preinstalled (build-essential, git, curl, python3),
# plus the few things a minimal image lacks.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl git bzip2 patch python3 procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/msr_finder
COPY . /opt/msr_finder
ARG INSTALL_ARGS=""
RUN bash install.sh ${INSTALL_ARGS} && rm -rf .build
RUN bash -c "source env.sh && ./smoke_test.sh"

# every shell in the container starts with env.sh loaded (install.sh added it to ~/.bashrc)
# What the README's commands create goes to /shared -- with  docker run -v "$PWD/shared:/shared"
# that is shared/ on the host: tables and figures in shared/tables, re-runs in shared/rerun.
ENV PAPER_OUT=/shared/tables RERUN_OUT=/shared/rerun
CMD ["bash"]

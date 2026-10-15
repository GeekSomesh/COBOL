# GnuCOBOL toolchain for the "docker" backend (COBOL_BACKEND=docker): compile and run
# programs in a container on hosts without WSL or a native cobc. Debian bookworm ships
# GnuCOBOL 3.1.2, the version the project is tested with.
FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends gnucobol coreutils \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home cobol
USER cobol
WORKDIR /home/cobol

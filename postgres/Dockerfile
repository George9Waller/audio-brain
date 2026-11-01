# Keep the PostgreSQL postmaster below a PID 1 reaper so timed-out Kubernetes exec probes cannot trigger crash recovery.
FROM pgvector/pgvector:pg18

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && tini --version \
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["postgres"]

# Container base image for the AutoDQA sandbox microVM.
# Lambda's snapshot-compatible Amazon Linux 2023 base (the MicroVM *OS* is provided
# separately via --base-image-arn at build time; this is the app container layer).
FROM public.ecr.aws/lambda/microvms:al2023-minimal

# Everything the sandbox needs is installed AT BUILD TIME so it is captured in the
# Firecracker snapshot. Installing at first use is what made the old sandbox's cold
# start race and time out (query_cdw used to `pip install python-tds` on demand).
#
#   python3/pip + python-tds : query_cdw talks to SQL Server over pure-Python TDS.
#   git + openssh-clients    : clone the ETL repo from GitLab over SSH. Verified
#                              reachable from the sandbox over the VPC egress
#                              connector -- gitpapl1.uth.tmc.edu:22 answers
#                              SSH-2.0-OpenSSH_7.4. openssh-clients also provides
#                              ssh-keyscan, which is how the host key gets captured
#                              for pinning (see README).
RUN dnf install -y python3 python3-pip git openssh-clients && dnf clean all \
 && pip3 install --no-cache-dir python-tds

# SSH needs a home for its config and a strict-permission dir, or it refuses the key.
# The private key itself is NEVER baked in: it is fetched from Secrets Manager per call
# and written here as 0600 by the injected runner, inside the ephemeral VM.
RUN mkdir -p /root/.ssh && chmod 700 /root/.ssh

WORKDIR /app
COPY server.py .

# NO ETL IS BAKED IN. It used to live at /opt/etl, which meant every ETL change needed
# a full cloud image rebuild before the served source matched what the database runs --
# and if that step was missed, an ETL-origin defect became unattributable because the
# agent read code that did not contain it. The sandbox now clones the synthetic ETL repo
# from GitLab on first use in a run (agentcore-gateway/lambda/etl_sandbox.py), so
# updating the ETL is a git push. git + openssh-clients above are what make that work.

# The code-exec server listens here; run-microvm's ingress connector fronts it.
EXPOSE 8080

# Lambda starts this via CMD, waits for readiness, then snapshots the running state.
CMD ["python3", "server.py"]

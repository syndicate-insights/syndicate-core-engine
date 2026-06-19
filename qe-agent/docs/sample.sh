# --- 1. Create secrets ---
kubectl -n qe-hack-syndicate create secret generic qe-quality-agent-secrets \
  --from-literal=NEO4J_PASSWORD='<NEO4J_PASSWORD>' \
  --from-literal=JIRA_API_TOKEN='<JIRA_API_TOKEN>' \
  --from-literal=HARNESS_API_KEY='<HARNESS_API_KEY>' \
  --from-literal=HARNESS_ACCOUNT_ID='<HARNESS_ACCOUNT_ID>' \
  --from-literal=GITHUB_TOKEN='<GITHUB_TOKEN>' \
  --from-literal=JIRA_WEBHOOK_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=HARNESS_BDD_WEBHOOK_URL='<HARNESS_BDD_WEBHOOK_URL>'

# --- 2. Apply manifests ---
kubectl -n qe-hack-syndicate apply -f qe-agent/deploy/k8s/configmap.yaml
kubectl -n qe-hack-syndicate apply -f qe-agent/deploy/k8s/deployment.yaml
kubectl -n qe-hack-syndicate apply -f qe-agent/deploy/k8s/service.yaml
kubectl -n qe-hack-syndicate apply -f qe-agent/deploy/k8s/ingress.yaml

# --- 3. Get the external load balancer IP ---
# Wait ~2 min after ingress apply for GCP to assign the IP
EXTERNAL_IP=$(kubectl -n qe-hack-syndicate get ingress qe-quality-agent-webhook \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo "External IP: $EXTERNAL_IP"

# --- 4. HTTPS via sslip.io (no domain required) ---
HOSTNAME=$(echo "$EXTERNAL_IP" | tr '.' '-').sslip.io
echo "Hostname: $HOSTNAME"

# Add host to ingress rule
kubectl -n qe-hack-syndicate patch ingress qe-quality-agent-webhook \
  --type=json \
  -p="[{\"op\":\"replace\",\"path\":\"/spec/rules/0/host\",\"value\":\"$HOSTNAME\"}]"

# Attach the pre-shared GCP SSL certificate named qe-agent
# (created in GCP Console -> Certificate Manager -> Certificates, or via gcloud)
kubectl -n qe-hack-syndicate annotate ingress qe-quality-agent-webhook \
  ingress.gcp.kubernetes.io/pre-shared-cert=qe-agent --overwrite

# Confirm the cert exists in GCP and covers the right domain:
gcloud certificate-manager certificates describe qe-agent --location=global

# Jira webhook URL to register:
echo "https://$HOSTNAME/qe/jira/webhook?token=4f0f6f53b12f2e2bcc385d2c14c41c969524e6a28b7068d2664fec6610619852"

# --- 5. (Optional) HTTPS via real domain (Cloud DNS) ---
PROJECT=project-3de3d3e7-4cce-44d9-b14
DOMAIN=qe-agent.astom.tools   # replace with your real domain

# Create DNS zone (skip if already exists)
gcloud dns managed-zones create qe-agent-zone \
  --dns-name="$DOMAIN." \
  --description="QE Agent webhook" \
  --project "$PROJECT"

# Point domain at ingress IP
gcloud dns record-sets create "$DOMAIN." \
  --zone=qe-agent-zone \
  --type=A \
  --ttl=60 \
  --rrdatas="$EXTERNAL_IP" \
  --project "$PROJECT"

# Patch ingress host
kubectl -n qe-hack-syndicate patch ingress qe-quality-agent-webhook \
  --type=json \
  -p="[{\"op\":\"replace\",\"path\":\"/spec/rules/0/host\",\"value\":\"$DOMAIN\"}]"

# Attach the pre-shared GCP SSL certificate named qe-agent
kubectl -n qe-hack-syndicate annotate ingress qe-quality-agent-webhook \
  ingress.gcp.kubernetes.io/pre-shared-cert=qe-agent --overwrite

# Confirm the cert exists in GCP:
gcloud certificate-manager certificates describe qe-agent --location=global

echo "https://$DOMAIN/qe/jira/webhook?token=<JIRA_WEBHOOK_TOKEN>"

# --- 6. HTTPS via subdomain in another GCP project ---
#
# Use this when your domain (managed zone) lives in a different GCP project
# to the one running the QE agent GKE cluster.
#
# Variables — fill in before running:
DNS_PROJECT=project-3de3d3e7-4cce-44d9-b14 # project that owns the domain/zone
GKE_PROJECT=project-61358164-b71e-4422-a5c  # project running the GKE cluster
SUBDOMAIN=qe-agent.astom.tools       # e.g. qe-agent.syndicate-insights.com
DNS_ZONE_NAME=astom-tools      # the Cloud DNS zone name in $DNS_PROJECT
                                             # (not the DNS name, the zone resource name)

# Step 6a: confirm the ingress external IP is ready
EXTERNAL_IP=$(kubectl -n qe-hack-syndicate get ingress qe-quality-agent-webhook \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo "External IP: $EXTERNAL_IP"

# Step 6b: add an A record for the subdomain in the other project's managed zone
gcloud dns record-sets create "${SUBDOMAIN}." \
  --zone="${DNS_ZONE_NAME}" \
  --type=A \
  --ttl=60 \
  --rrdatas="${EXTERNAL_IP}" \
  --project "${DNS_PROJECT}"

# Verify propagation (expect the IP back within a few minutes):
dig +short "${SUBDOMAIN}"

# Step 6c: patch the ingress host rule to match the subdomain
kubectl -n qe-hack-syndicate patch ingress qe-quality-agent-webhook \
  --type=json \
  -p="[{\"op\":\"replace\",\"path\":\"/spec/rules/0/host\",\"value\":\"${SUBDOMAIN}\"}]"

# Step 6d: attach the pre-shared GCP SSL certificate named qe-agent.
# The cert must already cover ${SUBDOMAIN}. Verify the domains it covers:
gcloud certificate-manager certificates describe qe-agent --location=global \
  --format='value(sanDescription)'
# If ${SUBDOMAIN} is not listed the cert will not serve that domain —
# you would need a new/updated GCP-managed cert covering it.

kubectl -n qe-hack-syndicate annotate ingress qe-quality-agent-webhook \
  ingress.gcp.kubernetes.io/pre-shared-cert=qe-agent --overwrite

# Step 6e: confirm GCP cert status is ACTIVE
gcloud certificate-manager certificates describe qe-agent --location=global \
  --format='value(state)'

# Step 6f: verify end-to-end
curl -i "https://${SUBDOMAIN}/healthz"

# Final Jira webhook URL:
echo "https://${SUBDOMAIN}/qe/jira/webhook?token=4f0f6f53b12f2e2bcc385d2c14c41c969524e6a28b7068d2664fec6610619852"

#
# Notes:
# - The DNS project just needs an A record — no GCP IAM cross-project permission
#   is required between the two projects for DNS.
# - The pre-shared GCP SSL certificate `qe-agent` is reused; no new cert is created.
# - If the zone uses NS delegation (e.g. subdomain delegated from a parent zone),
#   ensure the NS records are correct first: `dig NS ${SUBDOMAIN}`
# - TTL 60s is used above for faster propagation during setup; raise to 300-3600
#   once stable.




gcloud builds submit --config deploy/cloudbuild.yaml \
  --substitutions _IMAGE=us-central1-docker.pkg.dev/project-61358164-b71e-4422-a5c/qe/qe-quality-agent:0.1.1 \
  --project <YOUR_PROJECT_ID> .


gcloud builds submit --config deploy/cloudbuild.yaml \
  --substitutions _IMAGE=us-central1-docker.pkg.dev/project-61358164-b71e-4422-a5c/qe/qe-quality-agent:0.1.2 \
  --project project-61358164-b71e-4422-a5c .


kubectl -n qe-hack-syndicate set image deployment/qe-quality-agent \
  qe-quality-agent=us-central1-docker.pkg.dev/project-61358164-b71e-4422-a5c/qe/qe-quality-agent:0.1.2



kubectl -n qe-hack-syndicate rollout status deployment/qe-quality-agent


kubectl -n qe-hack-syndicate rollout restart deployment/qe-quality-agent
kubectl -n qe-hack-syndicate logs deploy/qe-quality-agent -f
#!/usr/bin/env bash
# fix-generated.sh — Patches known code-generation bugs in generated Go files.
#
# The ACK code-generator produces incorrect pointer handling for VpcOptions
# fields in vpc_attachment/sdk.go. The AWS SDK v2 defines these fields as *bool,
# but the generator treats them as value types, producing:
#   - &resp.VpcAttachment.Options.<Field>   → **bool (should omit &)
#   - *r.ko.Spec.Options.<Field>            → bool  (should omit *)
#
# This script fixes both patterns. Run it after every `ack-generate controller`.
# See docs/codegen-bug-vpc-options.md for the upstream bug report.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SDK_FILE="${ROOT_DIR}/pkg/resource/vpc_attachment/sdk.go"

if [[ ! -f "${SDK_FILE}" ]]; then
  echo "ERROR: ${SDK_FILE} not found. Run ack-generate controller first."
  exit 1
fi

echo "Fixing VpcOptions *bool pointer bug in vpc_attachment/sdk.go..."

# Fix 1: Remove & prefix when reading *bool fields from SDK response
# &resp.VpcAttachment.Options.ApplianceModeSupport → resp.VpcAttachment.Options.ApplianceModeSupport
for field in ApplianceModeSupport DnsSupport Ipv6Support SecurityGroupReferencingSupport; do
  sed -i '' "s|= &resp\.VpcAttachment\.Options\.${field}|= resp.VpcAttachment.Options.${field}|g" "${SDK_FILE}"
done

# Fix 2: Remove * dereference when writing *bool fields to SDK input
# *r.ko.Spec.Options.ApplianceModeSupport → r.ko.Spec.Options.ApplianceModeSupport
for field in ApplianceModeSupport DNSSupport IPv6Support SecurityGroupReferencingSupport; do
  sed -i '' "s|= \*r\.ko\.Spec\.Options\.${field}|= r.ko.Spec.Options.${field}|g" "${SDK_FILE}"
done

# Fix 3: Add missing top-level `state` field to attachment CRD schemas.
# The code generator does not emit a top-level `status.state` property for
# attachment resources even though the Go types define it (via the `from`
# directive with a nested path). Without it, the API server logs
# `unknown field "status.state"` warnings whenever the controller sets it.
echo "Adding missing status.state field to attachment CRD schemas..."
CRD_DIRS=("${ROOT_DIR}/helm/crds" "${ROOT_DIR}/config/crd/bases")
CRD_PATTERNS=(
  "networkmanager.services.k8s.aws_vpcattachments.yaml"
  "networkmanager.services.k8s.aws_connectattachments.yaml"
  "networkmanager.services.k8s.aws_sitetositevpnattachments.yaml"
)
for dir in "${CRD_DIRS[@]}"; do
  for pattern in "${CRD_PATTERNS[@]}"; do
    crd_file="${dir}/${pattern}"
    if [[ ! -f "${crd_file}" ]]; then
      echo "  SKIP (not found): ${crd_file}"
      continue
    fi
    # Idempotent: only insert if top-level state field is missing
    if grep -q '^              state:' "${crd_file}"; then
      echo "  OK (already has state): ${crd_file}"
      continue
    fi
    # Insert state field before the conditions block at 14-space indentation
    sed -i '' '/^              conditions:/i\
              state:\
                description: The state of the attachment.\
                type: string' "${crd_file}"
    echo "  PATCHED: ${crd_file}"
  done
done

echo "Fixing imports across generated files..."
GOIMPORTS="$(command -v goimports 2>/dev/null || echo "")"
if [[ -z "${GOIMPORTS}" && -x "$(go env GOPATH)/bin/goimports" ]]; then
  GOIMPORTS="$(go env GOPATH)/bin/goimports"
fi

if [[ -n "${GOIMPORTS}" ]]; then
  "${GOIMPORTS}" -w "${ROOT_DIR}/pkg/resource/"
else
  echo "ERROR: goimports not found. Install with: go install golang.org/x/tools/cmd/goimports@latest"
  exit 1
fi

echo "Done. Run 'go build ./...' to verify."

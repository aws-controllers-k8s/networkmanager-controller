package site_to_site_vpn_attachment

import (
	"context"

	svcapitypes "github.com/aws-controllers-k8s/networkmanager-controller/apis/v1alpha1"
	svcsdk "github.com/aws/aws-sdk-go-v2/service/networkmanager"
)

// customDeleteSiteToSiteVpnAttachment deletes a SiteToSiteVpnAttachment using
// the shared DeleteAttachment API.
func (rm *resourceManager) customDeleteSiteToSiteVpnAttachment(
	ctx context.Context,
	r *resource,
) (*resource, error) {
	attachmentID := r.ko.Status.AttachmentID
	if attachmentID == nil {
		return nil, nil
	}
	input := &svcsdk.DeleteAttachmentInput{
		AttachmentId: attachmentID,
	}
	_, err := rm.sdkapi.DeleteAttachment(ctx, input)
	rm.metrics.RecordAPICall("DELETE", "DeleteAttachment", err)
	return nil, err
}

// syncTopLevelStatus copies AttachmentID and State from the nested
// Attachment struct to the top-level Status fields.
// See vpc_attachment/hook.go for the full explanation.
func (rm *resourceManager) syncTopLevelStatus(ko *svcapitypes.SiteToSiteVPNAttachment) {
	if ko.Status.Attachment != nil {
		ko.Status.AttachmentID = ko.Status.Attachment.AttachmentID
		ko.Status.State = ko.Status.Attachment.State
	}
}

// Reference to avoid unused import error
var _ = &svcapitypes.SiteToSiteVPNAttachment{}

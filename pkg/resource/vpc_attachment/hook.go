package vpc_attachment

import (
	"context"

	svcapitypes "github.com/aws-controllers-k8s/networkmanager-controller/apis/v1alpha1"
	svcsdk "github.com/aws/aws-sdk-go-v2/service/networkmanager"
)

// customDeleteVpcAttachment deletes a VpcAttachment using the shared
// DeleteAttachment API.
func (rm *resourceManager) customDeleteVpcAttachment(
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
// Attachment struct to the top-level Status fields. The code generator's
// `from` directive does not produce mappings for these fields because they
// are derived from a nested response path (e.g. VpcAttachment.Attachment.AttachmentId).
// Without this, requiredFieldsMissingFromReadOneInput always returns true
// after Create, causing the controller to never call GetVpcAttachment and
// instead retry CreateVpcAttachment — which fails with "already attached".
func (rm *resourceManager) syncTopLevelStatus(ko *svcapitypes.VPCAttachment) {
	if ko.Status.Attachment != nil {
		ko.Status.AttachmentID = ko.Status.Attachment.AttachmentID
		ko.Status.State = ko.Status.Attachment.State
	}
}

// Reference to avoid unused import error
var _ = &svcapitypes.VPCAttachment{}

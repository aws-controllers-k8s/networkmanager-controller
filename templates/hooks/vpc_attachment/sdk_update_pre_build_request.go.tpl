	// Status.ResourceARN is not usable here: despite its "attachment
	// resource ARN" documentation, the live API returns the ARN of the
	// attached resource (the VPC itself), not the attachment. TagResource
	// requires the attachment's own ARN, which attachmentARN derives from
	// CoreNetworkARN + AttachmentID. See attachmentARN's doc comment in
	// hook.go.
	if delta.DifferentAt("Spec.Tags") {
		if arn := attachmentARN(latest.ko.Status.Attachment); arn != nil {
			if err := syncTags(
				ctx, rm.sdkapi, rm.metrics, *arn,
				desired.ko.Spec.Tags, latest.ko.Spec.Tags,
			); err != nil {
				return nil, err
			}
		}
	}
	if !delta.DifferentExcept("Spec.Tags") {
		// Only tags changed. UpdateVpcAttachment has no Tags field and must
		// not be called for a tag-only change.
		return desired, nil
	}

	// UpdateVpcAttachmentInput models subnet membership as additions and
	// removals (AddSubnetArns/RemoveSubnetArns), not a full replacement list,
	// and the field names do not match Spec.SubnetARNs, so the generator
	// cannot auto-populate them from the resource's desired list. Compute
	// the exact delta against the latest observed state instead; treating
	// Spec.SubnetARNs as a full replacement would re-add every subnet on
	// every reconcile and could race with concurrent membership changes.
	if delta.DifferentAt("Spec.SubnetARNs") {
		toAdd, toRemove := subnetARNDelta(desired.ko.Spec.SubnetARNs, latest.ko.Spec.SubnetARNs)
		input.AddSubnetArns = toAdd
		input.RemoveSubnetArns = toRemove
	}

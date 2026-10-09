// Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License"). You may
// not use this file except in compliance with the License. A copy of the
// License is located at
//
//     http://aws.amazon.com/apache2.0/
//
// or in the "license" file accompanying this file. This file is distributed
// on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
// express or implied. See the License for the specific language governing
// permissions and limitations under the License.

package vpc_attachment

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/aws/smithy-go"

	ackrequeue "github.com/aws-controllers-k8s/runtime/pkg/requeue"

	svcapitypes "github.com/aws-controllers-k8s/networkmanager-controller/apis/v1alpha1"
	"github.com/aws-controllers-k8s/networkmanager-controller/pkg/tags"
	svcsdk "github.com/aws/aws-sdk-go-v2/service/networkmanager"
)

// syncTags reuses the controller's shared tag-sync helper. VpcAttachment has
// no TagResource/UntagResource of its own; both operations are keyed on the
// attachment's own ARN, derived by attachmentARN below.
var syncTags = tags.SyncTags

const attachmentStateInvalidMessage = "Core network attachment state is invalid"

// isAttachmentStateTransitionError identifies the validation response returned
// when DeleteAttachment races an in-progress Cloud WAN state transition. The
// same ValidationException code is also used for malformed input, so only this
// specific recoverable message is requeued.
func isAttachmentStateTransitionError(err error) bool {
	var apiErr smithy.APIError
	return errors.As(err, &apiErr) &&
		apiErr.ErrorCode() == "ValidationException" &&
		strings.Contains(apiErr.ErrorMessage(), attachmentStateInvalidMessage)
}

// customDeleteVpcAttachment deletes a VpcAttachment through the generic
// DeleteAttachment API. VpcAttachment has no dedicated delete operation;
// every Network Manager attachment type shares this one call, keyed on the
// observed attachment identifier.
//
// Returning (nil, nil) when AttachmentID is unset mirrors the generated
// not-found/no-op short-circuit used elsewhere in this controller: if AWS
// never returned an identifier (for example, Create failed before an
// attachment existed), there is nothing to delete and the finalizer may be
// released immediately.
func (rm *resourceManager) customDeleteVpcAttachment(
	ctx context.Context,
	r *resource,
) (*resource, error) {
	if r.ko.Status.AttachmentID == nil {
		return nil, nil
	}
	input := &svcsdk.DeleteAttachmentInput{
		AttachmentId: r.ko.Status.AttachmentID,
	}
	_, err := rm.sdkapi.DeleteAttachment(ctx, input)
	rm.metrics.RecordAPICall("DELETE", "DeleteAttachment", err)
	if err != nil {
		if isAttachmentStateTransitionError(err) {
			return r, ackrequeue.NeededAfter(nil, 15*time.Second)
		}
		return nil, err
	}
	// DeleteAttachment is asynchronous: a successful call starts the AWS-side
	// transition to the DELETING state rather than completing deletion
	// immediately. The ACK runtime's deleteResource removes the finalizer as
	// soon as Delete returns a nil error, regardless of AWS-side state, so a
	// plain (r, nil) here would drop the finalizer before deletion is
	// confirmed, violating Requirement 4.5. Returning a requeue signal
	// instead keeps the finalizer and defers removal to a later
	// reconciliation, where ReadOne's ResourceNotFoundException handling
	// releases it once AWS confirms the attachment is gone.
	return r, ackrequeue.NeededAfter(nil, 15*time.Second)
}

// syncTopLevelStatus promotes AttachmentID, State, and ResourceARN from the
// nested Status.Attachment struct to their top-level Status fields, and
// refreshes Spec.Tags from AWS reality.
//
// AttachmentID and State appear in VpcAttachment.Attachment.* in every
// Create/Read/Update response. The code generator's `from:` directive in
// generator.yaml resolves the Go type for these Status fields but does not
// by itself emit the value-copy code for every operation's output shape.
// Without this hook, requiredFieldsMissingFromReadOneInput stays true after
// Create (AttachmentID is never populated), so the controller never calls
// GetVpcAttachment and instead retries CreateVpcAttachment, which fails with
// a "VPC is already attached" error. This is a known code-generator gap
// (see design.md "Current State and Entry Gate"); correcting it in a hook
// avoids the post-generation patch script that an earlier prior-art PR used
// to repair generated files directly.
//
// Status.ResourceARN is promoted as documented ("the attachment resource
// ARN"), but live testing against the real API shows it is actually the ARN
// of the *attached resource* -- for a VpcAttachment this is the VPC's own
// EC2 ARN (arn:...:ec2:...:vpc/vpc-xxx), confirmed via ListTagsForResource
// returning ResourceNotFoundException when called with this value. It must
// not be used as the tag-sync target; see attachmentARN below for the ARN
// TagResource/UntagResource/ListTagsForResource actually require.
//
// Tags are nested the same way (VpcAttachment.Attachment.Tags), but unlike
// AttachmentID/State they are not configured via a `from:` directive,
// because Spec.Tags -- not a Status field -- is the generator's natural
// target for a top-level "Tags" response member. The generator only wires
// that assignment automatically when the SDK response shape's Tags member
// sits at the top level (as with GlobalNetwork's CreateGlobalNetworkOutput);
// here it is nested under Attachment, so sdkFind/sdkCreate never populate
// Spec.Tags from AWS. Left unset, Spec.Tags only ever reflects the last
// desired value the controller itself wrote, so delta.DifferentAt("Spec.Tags")
// never observes a tag added or removed directly in AWS, and -- more
// importantly -- requeued reconciles of a user-initiated tag change can
// compare the new desired tags against a stale latest.ko.Spec.Tags that was
// never confirmed applied. Promoting Tags here, from the same
// Status.Attachment.Tags source State/AttachmentID already use, keeps
// Spec.Tags synced to AWS reality after every Create/Read/Update, so
// syncTags is driven off accurate drift.
func (rm *resourceManager) syncTopLevelStatus(ko *svcapitypes.VPCAttachment) {
	if ko.Status.Attachment == nil {
		return
	}
	ko.Status.AttachmentID = ko.Status.Attachment.AttachmentID
	ko.Status.State = ko.Status.Attachment.State
	ko.Status.ResourceARN = ko.Status.Attachment.ResourceARN
	ko.Spec.Tags = ko.Status.Attachment.Tags
}

// attachmentARN returns the attachment's own ARN
// (arn:<partition>:networkmanager::<account>:attachment/<attachmentId>),
// which is the identifier TagResource, UntagResource, and
// ListTagsForResource require for a VpcAttachment.
//
// The Network Manager API has no response field that returns this ARN
// directly: Attachment.ResourceArn is documented as "the attachment
// resource ARN" but is actually the ARN of the resource the attachment
// connects (the VPC itself for a VpcAttachment), confirmed empirically
// against the live API. GetVpcAttachment/CreateVpcAttachment do return
// Attachment.CoreNetworkARN, a full, correctly-partitioned ARN for the same
// account in the same service (arn:<partition>:networkmanager::<account>:
// core-network/<id>). Deriving the attachment ARN by swapping its resource
// segment avoids hardcoding a partition literal, so this still produces a
// correct ARN in non-aws partitions (for example aws-us-gov).
func attachmentARN(attachment *svcapitypes.Attachment) *string {
	if attachment == nil || attachment.CoreNetworkARN == nil || attachment.AttachmentID == nil {
		return nil
	}
	idx := strings.LastIndex(*attachment.CoreNetworkARN, ":core-network/")
	if idx == -1 {
		return nil
	}
	arn := (*attachment.CoreNetworkARN)[:idx] + ":attachment/" + *attachment.AttachmentID
	return &arn
}

// subnetARNDelta compares the desired and latest observed subnet ARN lists
// and returns the exact sets of ARNs to add and remove, for use with
// UpdateVpcAttachmentInput's AddSubnetArns and RemoveSubnetArns fields.
//
// UpdateVpcAttachment has no "replace the whole list" operation; sending the
// full desired list as an addition would be rejected for ARNs that are
// already attached, and would never remove an ARN that the user dropped
// from Spec. Computing the precise delta here, rather than treating the
// desired list as a full replacement, also avoids re-sending unchanged
// subnets on every reconcile, which could race with a concurrent membership
// change made outside the controller.
func subnetARNDelta(desired, latest []*string) (toAdd, toRemove []string) {
	desiredSet := make(map[string]bool, len(desired))
	for _, arn := range desired {
		if arn != nil {
			desiredSet[*arn] = true
		}
	}
	latestSet := make(map[string]bool, len(latest))
	for _, arn := range latest {
		if arn != nil {
			latestSet[*arn] = true
		}
	}
	for arn := range desiredSet {
		if !latestSet[arn] {
			toAdd = append(toAdd, arn)
		}
	}
	for arn := range latestSet {
		if !desiredSet[arn] {
			toRemove = append(toRemove, arn)
		}
	}
	return toAdd, toRemove
}

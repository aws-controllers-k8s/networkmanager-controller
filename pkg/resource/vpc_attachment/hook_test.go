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
	"sort"
	"testing"

	ackcompare "github.com/aws-controllers-k8s/runtime/pkg/compare"
	"github.com/aws/smithy-go"

	svcapitypes "github.com/aws-controllers-k8s/networkmanager-controller/apis/v1alpha1"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func strPtr(s string) *string { return &s }

// sortedCopy returns a sorted copy of a string slice so test assertions do
// not depend on map iteration order.
func sortedCopy(s []string) []string {
	out := append([]string{}, s...)
	sort.Strings(out)
	return out
}

func TestSubnetARNDelta(t *testing.T) {
	tests := []struct {
		name         string
		desired      []*string
		latest       []*string
		wantToAdd    []string
		wantToRemove []string
	}{
		{
			name:         "no change",
			desired:      []*string{strPtr("arn:subnet-1"), strPtr("arn:subnet-2")},
			latest:       []*string{strPtr("arn:subnet-1"), strPtr("arn:subnet-2")},
			wantToAdd:    nil,
			wantToRemove: nil,
		},
		{
			name:         "add one subnet",
			desired:      []*string{strPtr("arn:subnet-1"), strPtr("arn:subnet-2")},
			latest:       []*string{strPtr("arn:subnet-1")},
			wantToAdd:    []string{"arn:subnet-2"},
			wantToRemove: nil,
		},
		{
			name:         "remove one subnet",
			desired:      []*string{strPtr("arn:subnet-1")},
			latest:       []*string{strPtr("arn:subnet-1"), strPtr("arn:subnet-2")},
			wantToAdd:    nil,
			wantToRemove: []string{"arn:subnet-2"},
		},
		{
			name:         "simultaneous add and remove",
			desired:      []*string{strPtr("arn:subnet-1"), strPtr("arn:subnet-3")},
			latest:       []*string{strPtr("arn:subnet-1"), strPtr("arn:subnet-2")},
			wantToAdd:    []string{"arn:subnet-3"},
			wantToRemove: []string{"arn:subnet-2"},
		},
		{
			name:         "empty desired removes everything",
			desired:      nil,
			latest:       []*string{strPtr("arn:subnet-1"), strPtr("arn:subnet-2")},
			wantToAdd:    nil,
			wantToRemove: []string{"arn:subnet-1", "arn:subnet-2"},
		},
		{
			name:         "empty latest adds everything",
			desired:      []*string{strPtr("arn:subnet-1"), strPtr("arn:subnet-2")},
			latest:       nil,
			wantToAdd:    []string{"arn:subnet-1", "arn:subnet-2"},
			wantToRemove: nil,
		},
		{
			name:         "both empty",
			desired:      nil,
			latest:       nil,
			wantToAdd:    nil,
			wantToRemove: nil,
		},
		{
			name:         "nil elements in either list are skipped, not treated as empty-string ARNs",
			desired:      []*string{strPtr("arn:subnet-1"), nil},
			latest:       []*string{nil, strPtr("arn:subnet-2")},
			wantToAdd:    []string{"arn:subnet-1"},
			wantToRemove: []string{"arn:subnet-2"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			toAdd, toRemove := subnetARNDelta(tt.desired, tt.latest)
			assert.Equal(t, sortedCopy(tt.wantToAdd), sortedCopy(toAdd), "toAdd mismatch")
			assert.Equal(t, sortedCopy(tt.wantToRemove), sortedCopy(toRemove), "toRemove mismatch")
		})
	}
}

// TestSubnetARNDelta_IdempotentRetry verifies that calling subnetARNDelta
// again after a delta has already been "applied" (latest now matches
// desired) produces no further changes, so a retried or requeued
// reconciliation does not resend a stale Add/Remove pair.
func TestSubnetARNDelta_IdempotentRetry(t *testing.T) {
	desired := []*string{strPtr("arn:subnet-1"), strPtr("arn:subnet-3")}
	latestBeforeApply := []*string{strPtr("arn:subnet-1"), strPtr("arn:subnet-2")}

	toAdd, toRemove := subnetARNDelta(desired, latestBeforeApply)
	assert.ElementsMatch(t, []string{"arn:subnet-3"}, toAdd)
	assert.ElementsMatch(t, []string{"arn:subnet-2"}, toRemove)

	// Simulate AWS having applied the update: latest now equals desired.
	latestAfterApply := desired
	toAdd, toRemove = subnetARNDelta(desired, latestAfterApply)
	assert.Empty(t, toAdd)
	assert.Empty(t, toRemove)
}

func TestSyncTopLevelStatus(t *testing.T) {
	tests := []struct {
		name            string
		attachment      *svcapitypes.Attachment
		wantAttachID    *string
		wantState       *string
		wantResourceARN *string
		wantTags        []*svcapitypes.Tag
	}{
		{
			name:            "nil attachment leaves top-level fields untouched",
			attachment:      nil,
			wantAttachID:    nil,
			wantState:       nil,
			wantResourceARN: nil,
			wantTags:        nil,
		},
		{
			name: "populated attachment promotes all three fields",
			attachment: &svcapitypes.Attachment{
				AttachmentID: strPtr("attachment-0123456789abcdef0"),
				State:        strPtr("AVAILABLE"),
				ResourceARN:  strPtr("arn:aws:networkmanager::123456789012:attachment/attachment-0123456789abcdef0"),
			},
			wantAttachID:    strPtr("attachment-0123456789abcdef0"),
			wantState:       strPtr("AVAILABLE"),
			wantResourceARN: strPtr("arn:aws:networkmanager::123456789012:attachment/attachment-0123456789abcdef0"),
			wantTags:        nil,
		},
		{
			name: "partially populated attachment (pending acceptance has no ResourceARN yet)",
			attachment: &svcapitypes.Attachment{
				AttachmentID: strPtr("attachment-0123456789abcdef0"),
				State:        strPtr("PENDING_ATTACHMENT_ACCEPTANCE"),
			},
			wantAttachID:    strPtr("attachment-0123456789abcdef0"),
			wantState:       strPtr("PENDING_ATTACHMENT_ACCEPTANCE"),
			wantResourceARN: nil,
			wantTags:        nil,
		},
		{
			// Tags live nested under Attachment in every Create/Read/Update
			// response, never at the top level, so there is no generator
			// `from:` directive that can wire Spec.Tags automatically (unlike
			// GlobalNetwork, whose Tags member is top-level). Without this
			// promotion, Spec.Tags only ever reflects the controller's last
			// desired write, delta.DifferentAt("Spec.Tags") never observes
			// drift against AWS, and syncTags (TagResource/UntagResource)
			// never fires.
			name: "populated attachment promotes tags into Spec.Tags",
			attachment: &svcapitypes.Attachment{
				AttachmentID: strPtr("attachment-0123456789abcdef0"),
				State:        strPtr("AVAILABLE"),
				Tags: []*svcapitypes.Tag{
					{Key: strPtr("env"), Value: strPtr("prod")},
				},
			},
			wantAttachID: strPtr("attachment-0123456789abcdef0"),
			wantState:    strPtr("AVAILABLE"),
			wantTags: []*svcapitypes.Tag{
				{Key: strPtr("env"), Value: strPtr("prod")},
			},
		},
		{
			// A tag removed in AWS (directly, or via a prior UntagResource
			// call) must clear from Spec.Tags too, not linger from a stale
			// previous sync.
			name: "attachment with no tags clears Spec.Tags",
			attachment: &svcapitypes.Attachment{
				AttachmentID: strPtr("attachment-0123456789abcdef0"),
				State:        strPtr("AVAILABLE"),
				Tags:         nil,
			},
			wantAttachID: strPtr("attachment-0123456789abcdef0"),
			wantState:    strPtr("AVAILABLE"),
			wantTags:     nil,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rm := &resourceManager{}
			ko := &svcapitypes.VPCAttachment{}
			ko.Status.Attachment = tt.attachment

			rm.syncTopLevelStatus(ko)

			assert.Equal(t, tt.wantAttachID, ko.Status.AttachmentID)
			assert.Equal(t, tt.wantState, ko.Status.State)
			assert.Equal(t, tt.wantResourceARN, ko.Status.ResourceARN)
			assert.Equal(t, tt.wantTags, ko.Spec.Tags)
		})
	}
}

// TestSyncTopLevelStatus_DoesNotClearOnNilAttachment verifies that a nil
// Status.Attachment (for example, a response shape that omitted it) does
// not wipe out previously-synced top-level fields. The hook should be a
// no-op in that case, not a reset.
func TestSyncTopLevelStatus_DoesNotClearOnNilAttachment(t *testing.T) {
	rm := &resourceManager{}
	ko := &svcapitypes.VPCAttachment{}
	ko.Status.AttachmentID = strPtr("attachment-0123456789abcdef0")
	ko.Status.State = strPtr("AVAILABLE")
	ko.Status.Attachment = nil

	rm.syncTopLevelStatus(ko)

	assert.Equal(t, strPtr("attachment-0123456789abcdef0"), ko.Status.AttachmentID)
	assert.Equal(t, strPtr("AVAILABLE"), ko.Status.State)
}

// TestAttachmentARN covers the ARN-derivation logic that corrects a
// production bug found during live AWS E2E testing: TagResource called with
// Status.ResourceARN (the attached VPC's own ARN, despite its "attachment
// resource ARN" documentation) returned a 404 ResourceNotFoundException.
// attachmentARN instead derives the attachment's own ARN from
// CoreNetworkARN + AttachmentID, which ListTagsForResource confirmed is the
// correct identifier for this resource type.
func TestAttachmentARN(t *testing.T) {
	tests := []struct {
		name       string
		attachment *svcapitypes.Attachment
		want       *string
	}{
		{
			name:       "nil attachment returns nil",
			attachment: nil,
			want:       nil,
		},
		{
			name: "populated attachment derives attachment ARN from CoreNetworkARN",
			attachment: &svcapitypes.Attachment{
				AttachmentID:   strPtr("attachment-00dc678834b8a2ba5"),
				CoreNetworkARN: strPtr("arn:aws:networkmanager::123456789012:core-network/core-network-092d1eca08b41291d"),
				// ResourceARN is the attached VPC's own ARN; it must not
				// appear in, or influence, the derived attachment ARN.
				ResourceARN: strPtr("arn:aws:ec2:us-east-1:123456789012:vpc/vpc-057864d613ddcfb6f"),
			},
			want: strPtr("arn:aws:networkmanager::123456789012:attachment/attachment-00dc678834b8a2ba5"),
		},
		{
			name: "non-aws partition is preserved from CoreNetworkARN rather than hardcoded",
			attachment: &svcapitypes.Attachment{
				AttachmentID:   strPtr("attachment-00dc678834b8a2ba5"),
				CoreNetworkARN: strPtr("arn:aws-us-gov:networkmanager::123456789012:core-network/core-network-092d1eca08b41291d"),
			},
			want: strPtr("arn:aws-us-gov:networkmanager::123456789012:attachment/attachment-00dc678834b8a2ba5"),
		},
		{
			name: "nil CoreNetworkARN returns nil",
			attachment: &svcapitypes.Attachment{
				AttachmentID: strPtr("attachment-00dc678834b8a2ba5"),
			},
			want: nil,
		},
		{
			name: "nil AttachmentID returns nil",
			attachment: &svcapitypes.Attachment{
				CoreNetworkARN: strPtr("arn:aws:networkmanager::123456789012:core-network/core-network-092d1eca08b41291d"),
			},
			want: nil,
		},
		{
			name: "CoreNetworkARN missing the expected core-network segment returns nil",
			attachment: &svcapitypes.Attachment{
				AttachmentID:   strPtr("attachment-00dc678834b8a2ba5"),
				CoreNetworkARN: strPtr("not-a-recognizable-arn"),
			},
			want: nil,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := attachmentARN(tt.attachment)
			if tt.want == nil {
				assert.Nil(t, got)
				return
			}
			require.NotNil(t, got)
			assert.Equal(t, *tt.want, *got)
		})
	}
}

func TestIsAttachmentStateTransitionError(t *testing.T) {
	tests := []struct {
		name string
		err  error
		want bool
	}{
		{
			name: "invalid attachment state is recoverable",
			err: &smithy.GenericAPIError{
				Code:    "ValidationException",
				Message: "Core network attachment state is invalid",
			},
			want: true,
		},
		{
			name: "wrapped invalid attachment state is recoverable",
			err: errors.Join(
				errors.New("delete failed"),
				&smithy.GenericAPIError{
					Code:    "ValidationException",
					Message: "Core network attachment state is invalid while updating",
				},
			),
			want: true,
		},
		{
			name: "different validation error remains terminal",
			err: &smithy.GenericAPIError{
				Code:    "ValidationException",
				Message: "Attachment identifier is malformed",
			},
			want: false,
		},
		{
			name: "different API error remains unchanged",
			err: &smithy.GenericAPIError{
				Code:    "AccessDeniedException",
				Message: "Access denied",
			},
			want: false,
		},
		{
			name: "non API error remains unchanged",
			err:  errors.New("network error"),
			want: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			assert.Equal(t, tt.want, isAttachmentStateTransitionError(tt.err))
		})
	}
}

func TestVpcOptionsRequestPayloads(t *testing.T) {
	applianceMode := false
	dnsSupport := true
	ipv6Support := false
	securityGroupReferencing := true
	attachmentID := "attachment-0123456789abcdef0"
	coreNetworkID := "core-network-0123456789abcdef0"
	vpcARN := "arn:aws:ec2:us-west-2:123456789012:vpc/vpc-0123456789abcdef0"

	r := &resource{ko: &svcapitypes.VPCAttachment{}}
	r.ko.Spec.CoreNetworkID = &coreNetworkID
	r.ko.Spec.VPCARN = &vpcARN
	r.ko.Spec.SubnetARNs = []*string{strPtr("arn:aws:ec2:us-west-2:123456789012:subnet/subnet-0123456789abcdef0")}
	r.ko.Spec.Options = &svcapitypes.VPCOptions{
		ApplianceModeSupport:            &applianceMode,
		DNSSupport:                      &dnsSupport,
		IPv6Support:                     &ipv6Support,
		SecurityGroupReferencingSupport: &securityGroupReferencing,
	}
	r.ko.Status.AttachmentID = &attachmentID

	rm := &resourceManager{}

	t.Run("create payload preserves explicit boolean values", func(t *testing.T) {
		input, err := rm.newCreateRequestPayload(context.Background(), r)
		require.NoError(t, err)
		require.NotNil(t, input.Options)
		assert.Same(t, r.ko.Spec.Options.ApplianceModeSupport, input.Options.ApplianceModeSupport)
		assert.Same(t, r.ko.Spec.Options.DNSSupport, input.Options.DnsSupport)
		assert.Same(t, r.ko.Spec.Options.IPv6Support, input.Options.Ipv6Support)
		assert.Same(t, r.ko.Spec.Options.SecurityGroupReferencingSupport, input.Options.SecurityGroupReferencingSupport)
		assert.False(t, *input.Options.ApplianceModeSupport)
		assert.True(t, *input.Options.DnsSupport)
		assert.False(t, *input.Options.Ipv6Support)
		assert.True(t, *input.Options.SecurityGroupReferencingSupport)
	})

	t.Run("update payload preserves explicit boolean values", func(t *testing.T) {
		input, err := rm.newUpdateRequestPayload(context.Background(), r, ackcompare.NewDelta())
		require.NoError(t, err)
		require.NotNil(t, input.Options)
		assert.Equal(t, &attachmentID, input.AttachmentId)
		assert.Same(t, r.ko.Spec.Options.ApplianceModeSupport, input.Options.ApplianceModeSupport)
		assert.Same(t, r.ko.Spec.Options.DNSSupport, input.Options.DnsSupport)
		assert.Same(t, r.ko.Spec.Options.IPv6Support, input.Options.Ipv6Support)
		assert.Same(t, r.ko.Spec.Options.SecurityGroupReferencingSupport, input.Options.SecurityGroupReferencingSupport)
	})
}

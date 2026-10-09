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

// SyncTags is pre-existing shared infrastructure (used by GlobalNetwork
// since the merged GlobalNetwork PR). It gained no unit test coverage at
// that time. VpcAttachment's tag-only update hook is its second consumer and
// the first to route through TagResource/UntagResource behind a per-call
// resource ARN rather than a cached one, so this test file closes that gap
// as part of satisfying Requirement 7.1/Task 10.2 for the VpcAttachment
// contribution, without changing SyncTags's behavior.
package tags

import (
	"context"
	"errors"
	"testing"

	"github.com/aws-controllers-k8s/networkmanager-controller/apis/v1alpha1"
	svcsdk "github.com/aws/aws-sdk-go-v2/service/networkmanager"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func tagPtr(key, value string) *v1alpha1.Tag {
	return &v1alpha1.Tag{Key: &key, Value: &value}
}

// fakeTagsClient is a test double for the tagsClient interface. It records
// every TagResource/UntagResource call it receives, in call order, so tests
// can assert on exactly what was sent to AWS.
type fakeTagsClient struct {
	tagCalls   []*svcsdk.TagResourceInput
	untagCalls []*svcsdk.UntagResourceInput
	tagErr     error
	untagErr   error
}

func (f *fakeTagsClient) TagResource(
	_ context.Context,
	input *svcsdk.TagResourceInput,
	_ ...func(*svcsdk.Options),
) (*svcsdk.TagResourceOutput, error) {
	f.tagCalls = append(f.tagCalls, input)
	return &svcsdk.TagResourceOutput{}, f.tagErr
}

func (f *fakeTagsClient) UntagResource(
	_ context.Context,
	input *svcsdk.UntagResourceInput,
	_ ...func(*svcsdk.Options),
) (*svcsdk.UntagResourceOutput, error) {
	f.untagCalls = append(f.untagCalls, input)
	return &svcsdk.UntagResourceOutput{}, f.untagErr
}

// fakeMetricsRecorder is a test double for the metricsRecorder interface
// that records every call for assertion.
type fakeMetricsRecorder struct {
	calls []struct {
		opType, opID string
		err          error
	}
}

func (f *fakeMetricsRecorder) RecordAPICall(opType, opID string, err error) {
	f.calls = append(f.calls, struct {
		opType, opID string
		err          error
	}{opType, opID, err})
}

func tagKeys(input *svcsdk.TagResourceInput) []string {
	keys := make([]string, 0, len(input.Tags))
	for _, t := range input.Tags {
		keys = append(keys, *t.Key)
	}
	return keys
}

func TestSyncTags_CreateAddsAllTags(t *testing.T) {
	// "Create" is modeled here as syncing against an empty latest/existing
	// tag set, matching how the controller first observes a freshly-created
	// resource with no prior tags.
	client := &fakeTagsClient{}
	mr := &fakeMetricsRecorder{}

	desired := []*v1alpha1.Tag{tagPtr("env", "prod"), tagPtr("team", "networking")}
	err := SyncTags(context.Background(), client, mr, "arn:aws:networkmanager::123456789012:attachment/attachment-1", desired, nil)

	require.NoError(t, err)
	require.Len(t, client.tagCalls, 1)
	assert.Empty(t, client.untagCalls)
	assert.ElementsMatch(t, []string{"env", "team"}, tagKeys(client.tagCalls[0]))
	assert.Equal(t, "arn:aws:networkmanager::123456789012:attachment/attachment-1", *client.tagCalls[0].ResourceArn)
}

func TestSyncTags_AddRemoveDelta(t *testing.T) {
	client := &fakeTagsClient{}
	mr := &fakeMetricsRecorder{}

	// "owner" is being removed, "team" is being added, "env" is unchanged
	// and must not appear in either call.
	desired := []*v1alpha1.Tag{tagPtr("env", "prod"), tagPtr("team", "networking")}
	existing := []*v1alpha1.Tag{tagPtr("env", "prod"), tagPtr("owner", "platform")}

	err := SyncTags(context.Background(), client, mr, "arn:test", desired, existing)

	require.NoError(t, err)
	require.Len(t, client.tagCalls, 1)
	require.Len(t, client.untagCalls, 1)
	assert.Equal(t, []string{"team"}, tagKeys(client.tagCalls[0]))
	assert.Equal(t, []string{"owner"}, client.untagCalls[0].TagKeys)
}

func TestSyncTags_ValueChangeIsAnAdd(t *testing.T) {
	// Changing a tag's value (not just presence) must re-send it through
	// TagResource; AWS tag APIs treat TagResource as an upsert, so no
	// corresponding UntagResource is needed for a value-only change.
	client := &fakeTagsClient{}
	mr := &fakeMetricsRecorder{}

	desired := []*v1alpha1.Tag{tagPtr("env", "staging")}
	existing := []*v1alpha1.Tag{tagPtr("env", "prod")}

	err := SyncTags(context.Background(), client, mr, "arn:test", desired, existing)

	require.NoError(t, err)
	require.Len(t, client.tagCalls, 1)
	assert.Empty(t, client.untagCalls)
	assert.Equal(t, []string{"env"}, tagKeys(client.tagCalls[0]))
	assert.Equal(t, "staging", *client.tagCalls[0].Tags[0].Value)
}

func TestSyncTags_NoopWhenTagsIdentical(t *testing.T) {
	client := &fakeTagsClient{}
	mr := &fakeMetricsRecorder{}

	tags := []*v1alpha1.Tag{tagPtr("env", "prod")}
	err := SyncTags(context.Background(), client, mr, "arn:test", tags, tags)

	require.NoError(t, err)
	assert.Empty(t, client.tagCalls)
	assert.Empty(t, client.untagCalls)
}

func TestSyncTags_RemoveAllTags(t *testing.T) {
	client := &fakeTagsClient{}
	mr := &fakeMetricsRecorder{}

	existing := []*v1alpha1.Tag{tagPtr("env", "prod"), tagPtr("team", "networking")}
	err := SyncTags(context.Background(), client, mr, "arn:test", nil, existing)

	require.NoError(t, err)
	assert.Empty(t, client.tagCalls)
	require.Len(t, client.untagCalls, 1)
	assert.ElementsMatch(t, []string{"env", "team"}, client.untagCalls[0].TagKeys)
}

func TestSyncTags_SurfacesTagResourceFailure(t *testing.T) {
	wantErr := errors.New("ThrottlingException")
	client := &fakeTagsClient{tagErr: wantErr}
	mr := &fakeMetricsRecorder{}

	desired := []*v1alpha1.Tag{tagPtr("env", "prod")}
	err := SyncTags(context.Background(), client, mr, "arn:test", desired, nil)

	assert.ErrorIs(t, err, wantErr)
	require.Len(t, mr.calls, 1)
	assert.Equal(t, "UPDATE", mr.calls[0].opType)
	assert.Equal(t, "TagResource", mr.calls[0].opID)
	assert.ErrorIs(t, mr.calls[0].err, wantErr)
}

func TestSyncTags_SurfacesUntagResourceFailure(t *testing.T) {
	wantErr := errors.New("AccessDeniedException")
	client := &fakeTagsClient{untagErr: wantErr}
	mr := &fakeMetricsRecorder{}

	existing := []*v1alpha1.Tag{tagPtr("env", "prod")}
	err := SyncTags(context.Background(), client, mr, "arn:test", nil, existing)

	assert.ErrorIs(t, err, wantErr)
	require.Len(t, mr.calls, 1)
	assert.Equal(t, "UntagResource", mr.calls[0].opID)
}

// TestSyncTags_DoesNotMutateInputSlices guards against a regression where
// SyncTags might reorder or mutate the caller's Spec.Tags/latest.ko.Spec.Tags
// slices in place, which would corrupt the ACK resource's observed state
// held elsewhere by the reconciler.
func TestSyncTags_DoesNotMutateInputSlices(t *testing.T) {
	client := &fakeTagsClient{}
	mr := &fakeMetricsRecorder{}

	desired := []*v1alpha1.Tag{tagPtr("env", "prod")}
	existing := []*v1alpha1.Tag{tagPtr("owner", "platform")}
	desiredLen, existingLen := len(desired), len(existing)

	err := SyncTags(context.Background(), client, mr, "arn:test", desired, existing)

	require.NoError(t, err)
	assert.Len(t, desired, desiredLen)
	assert.Len(t, existing, existingLen)
	assert.Equal(t, "env", *desired[0].Key)
	assert.Equal(t, "owner", *existing[0].Key)
}

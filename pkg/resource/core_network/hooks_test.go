package core_network

import (
	"context"
	"errors"
	"testing"

	svcapitypes "github.com/aws-controllers-k8s/networkmanager-controller/apis/v1alpha1"
	"github.com/aws/aws-sdk-go-v2/aws"
	svcsdk "github.com/aws/aws-sdk-go-v2/service/networkmanager"
	svcsdktypes "github.com/aws/aws-sdk-go-v2/service/networkmanager/types"
	smithy "github.com/aws/smithy-go"
)

type mockCoreNetworkPolicyClient struct {
	getOutput     *svcsdk.GetCoreNetworkPolicyOutput
	getErr        error
	putErr        error
	executeErr    error
	getInputs     []*svcsdk.GetCoreNetworkPolicyInput
	putInputs     []*svcsdk.PutCoreNetworkPolicyInput
	executeInputs []*svcsdk.ExecuteCoreNetworkChangeSetInput
}

func (m *mockCoreNetworkPolicyClient) GetCoreNetworkPolicy(_ context.Context, input *svcsdk.GetCoreNetworkPolicyInput, _ ...func(*svcsdk.Options)) (*svcsdk.GetCoreNetworkPolicyOutput, error) {
	m.getInputs = append(m.getInputs, input)
	return m.getOutput, m.getErr
}

func (m *mockCoreNetworkPolicyClient) PutCoreNetworkPolicy(_ context.Context, input *svcsdk.PutCoreNetworkPolicyInput, _ ...func(*svcsdk.Options)) (*svcsdk.PutCoreNetworkPolicyOutput, error) {
	m.putInputs = append(m.putInputs, input)
	if m.putErr != nil {
		return nil, m.putErr
	}
	return &svcsdk.PutCoreNetworkPolicyOutput{}, nil
}

func (m *mockCoreNetworkPolicyClient) ExecuteCoreNetworkChangeSet(_ context.Context, input *svcsdk.ExecuteCoreNetworkChangeSetInput, _ ...func(*svcsdk.Options)) (*svcsdk.ExecuteCoreNetworkChangeSetOutput, error) {
	m.executeInputs = append(m.executeInputs, input)
	if m.executeErr != nil {
		return nil, m.executeErr
	}
	return &svcsdk.ExecuteCoreNetworkChangeSetOutput{}, nil
}

type stubAPIError struct {
	code    string
	message string
	fault   smithy.ErrorFault
}

func (e stubAPIError) Error() string {
	return e.code + ": " + e.message
}

func (e stubAPIError) ErrorCode() string {
	return e.code
}

func (e stubAPIError) ErrorMessage() string {
	return e.message
}

func (e stubAPIError) ErrorFault() smithy.ErrorFault {
	return e.fault
}

func TestSyncPolicyDocument(t *testing.T) {
	getErr := errors.New("get failed")
	putErr := errors.New("put failed")
	executeErr := errors.New("execute failed")
	policyNotFoundErr := stubAPIError{code: "ValidationException", message: "Policy not found", fault: smithy.FaultClient}

	tests := []struct {
		name         string
		client       *mockCoreNetworkPolicyClient
		desired      *resource
		live         *resource
		wantErr      error
		assertResult func(*testing.T, *mockCoreNetworkPolicyClient)
	}{
		{
			name: "same version same document returns nil",
			client: &mockCoreNetworkPolicyClient{
				getOutput: newGetPolicyOutput(t, 7, `{"a":1,"b":[2, 3]}`, ""),
			},
			desired: newCoreNetworkResource(t, "core-network-123", 0, `{"b":[2,3],"a":1}`),
			live:    newCoreNetworkResource(t, "core-network-123", 7, `{"b":[2,3],"a":1}`),
			assertResult: func(t *testing.T, client *mockCoreNetworkPolicyClient) {
				assertPolicyCalls(t, client, 1, 0, 0)
				if got := client.getInputs[0].Alias; got != svcsdktypes.CoreNetworkPolicyAliasLatest {
					t.Fatalf("expected latest alias, got %v", got)
				}
			},
		},
		{
			name: "same version different document puts policy",
			client: &mockCoreNetworkPolicyClient{
				getOutput: newGetPolicyOutput(t, 3, `{"segments":[{"name":"blue"}]}`, ""),
			},
			desired: newCoreNetworkResource(t, "core-network-123", 0, `{"segments":[{"name":"green"}]}`),
			live:    newCoreNetworkResource(t, "core-network-123", 3, ""),
			wantErr: requeueWaitWhilePolicyDocumentUpdating,
			assertResult: func(t *testing.T, client *mockCoreNetworkPolicyClient) {
				assertPolicyCalls(t, client, 1, 1, 0)
				if got := *client.putInputs[0].PolicyDocument; got != `{"segments":[{"name":"green"}]}` {
					t.Fatalf("expected updated policy document, got %q", got)
				}
			},
		},
		{
			name: "no latest policy puts desired document",
			client: &mockCoreNetworkPolicyClient{
				getErr: stubAPIError{code: "ValidationException", message: "no policy", fault: smithy.FaultClient},
			},
			desired: newCoreNetworkResource(t, "core-network-123", 0, `{"segments":[{"name":"initial"}]}`),
			live:    newCoreNetworkResource(t, "core-network-123", 0, ""),
			wantErr: requeueWaitWhilePolicyDocumentUpdating,
			assertResult: func(t *testing.T, client *mockCoreNetworkPolicyClient) {
				assertPolicyCalls(t, client, 1, 1, 0)
			},
		},
		{
			name: "different version pending generation returns requeue",
			client: &mockCoreNetworkPolicyClient{
				getOutput: newGetPolicyOutput(t, 11, `{"segments":[]}`, svcsdktypes.ChangeSetStatePendingGeneration),
			},
			desired: newCoreNetworkResource(t, "core-network-123", 0, `{"segments":[]}`),
			live:    newCoreNetworkResource(t, "core-network-123", 10, ""),
			wantErr: requeueWaitWhilePolicyDocumentGenerating,
			assertResult: func(t *testing.T, client *mockCoreNetworkPolicyClient) {
				assertPolicyCalls(t, client, 1, 0, 0)
			},
		},
		{
			name: "different version ready to execute runs change set",
			client: &mockCoreNetworkPolicyClient{
				getOutput: newGetPolicyOutput(t, 11, `{"segments":[]}`, svcsdktypes.ChangeSetStateReadyToExecute),
			},
			desired: newCoreNetworkResource(t, "core-network-123", 0, `{"segments":[]}`),
			live:    newCoreNetworkResource(t, "core-network-123", 10, ""),
			wantErr: requeueWaitWhilePolicyDocumentExecuting,
			assertResult: func(t *testing.T, client *mockCoreNetworkPolicyClient) {
				assertPolicyCalls(t, client, 1, 0, 1)
				if got := *client.executeInputs[0].PolicyVersionId; got != 11 {
					t.Fatalf("expected policy version 11, got %d", got)
				}
			},
		},
		{
			name: "different version executing returns requeue",
			client: &mockCoreNetworkPolicyClient{
				getOutput: newGetPolicyOutput(t, 11, `{"segments":[]}`, svcsdktypes.ChangeSetStateExecuting),
			},
			desired: newCoreNetworkResource(t, "core-network-123", 0, `{"segments":[]}`),
			live:    newCoreNetworkResource(t, "core-network-123", 10, ""),
			wantErr: requeueWaitWhilePolicyDocumentExecuting,
			assertResult: func(t *testing.T, client *mockCoreNetworkPolicyClient) {
				assertPolicyCalls(t, client, 1, 0, 0)
			},
		},
		{
			name:    "get policy error returns error",
			client:  &mockCoreNetworkPolicyClient{getErr: getErr},
			desired: newCoreNetworkResource(t, "core-network-123", 0, ""), //`{"segments":[]}`),
			live:    newCoreNetworkResource(t, "core-network-123", 0, ""),
			wantErr: getErr,
			assertResult: func(t *testing.T, client *mockCoreNetworkPolicyClient) {
				assertPolicyCalls(t, client, 1, 0, 0)
			},
		},
		{
			name:    "no live policy returns requeue",
			client:  &mockCoreNetworkPolicyClient{getErr: policyNotFoundErr},
			desired: newCoreNetworkResource(t, "core-network-123", 0, `{"segments":[]}`),
			live:    newCoreNetworkResource(t, "core-network-123", 0, ""),
			wantErr: requeueWaitWhilePolicyDocumentUpdating,
			assertResult: func(t *testing.T, client *mockCoreNetworkPolicyClient) {
				assertPolicyCalls(t, client, 1, 1, 0)
			},
		},
		{
			name: "put policy error returns error",
			client: &mockCoreNetworkPolicyClient{
				getOutput: newGetPolicyOutput(t, 3, `{"segments":[{"name":"blue"}]}`, ""),
				putErr:    putErr,
			},
			desired: newCoreNetworkResource(t, "core-network-123", 0, `{"segments":[{"name":"green"}]}`),
			live:    newCoreNetworkResource(t, "core-network-123", 3, ""),
			wantErr: putErr,
			assertResult: func(t *testing.T, client *mockCoreNetworkPolicyClient) {
				assertPolicyCalls(t, client, 1, 1, 0)
			},
		},
		{
			name: "execute change set error returns error",
			client: &mockCoreNetworkPolicyClient{
				getOutput:  newGetPolicyOutput(t, 11, `{"segments":[]}`, svcsdktypes.ChangeSetStateReadyToExecute),
				executeErr: executeErr,
			},
			desired: newCoreNetworkResource(t, "core-network-123", 0, `{"segments":[]}`),
			live:    newCoreNetworkResource(t, "core-network-123", 10, ""),
			wantErr: executeErr,
			assertResult: func(t *testing.T, client *mockCoreNetworkPolicyClient) {
				assertPolicyCalls(t, client, 1, 0, 1)
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := syncPolicyDocument(context.Background(), tt.client, tt.desired, tt.live)
			if !errors.Is(err, tt.wantErr) {
				t.Fatalf("expected error %v, got %v", tt.wantErr, err)
			}
			tt.assertResult(t, tt.client)
		})
	}
}

func TestArePolicyDocumentsEqual(t *testing.T) {
	tests := []struct {
		name string
		a    *string
		b    *string
		want bool
	}{
		{
			name: "semantic JSON equality",
			a:    aws.String(`{"version":"2021.12","segments":[{"name":"blue"}],"asnRanges":["64512-65534"]}`),
			b:    aws.String(`{"asnRanges": ["64512-65534"],"segments": [{"name": "blue"}],"version": "2021.12"}`),
			want: true,
		},
		{
			name: "different JSON",
			a:    aws.String(`{"version":"2021.12","segments":[{"name":"blue"}],"asnRanges":["64512-65534"]}`),
			b:    aws.String(`{"asnRanges": ["64512-65534"],"segments": [{"name": "green"}],"version": "2021.12"}`),
			want: false,
		},
		{
			name: "invalid JSON identical strings",
			a:    aws.String(`not-json`),
			b:    aws.String(`not-json`),
			want: true,
		},
		{
			name: "invalid JSON different strings",
			a:    aws.String(`not-json`),
			b:    aws.String(`still-not-json`),
			want: false,
		},
		{
			name: "one nil one empty string",
			a:    nil,
			b:    aws.String(``),
			want: true,
		},
		{
			name: "both nil",
			a:    nil,
			b:    nil,
			want: true,
		},
		{
			name: "one nil one non-empty string",
			a:    nil,
			b:    aws.String(`non-empty`),
			want: false,
		},
		{
			name: "both empty strings",
			a:    aws.String(``),
			b:    aws.String(``),
			want: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := arePolicyDocumentsEqual(tt.a, tt.b); got != tt.want {
				t.Fatalf("expected %v, got %v", tt.want, got)
			}
		})
	}
}

func assertPolicyCalls(t *testing.T, client *mockCoreNetworkPolicyClient, wantGet int, wantPut int, wantExecute int) {
	t.Helper()
	if len(client.getInputs) != wantGet {
		t.Fatalf("expected %d GetCoreNetworkPolicy calls, got %d", wantGet, len(client.getInputs))
	}
	if len(client.putInputs) != wantPut {
		t.Fatalf("expected %d PutCoreNetworkPolicy calls, got %d", wantPut, len(client.putInputs))
	}
	if len(client.executeInputs) != wantExecute {
		t.Fatalf("expected %d ExecuteCoreNetworkChangeSet calls, got %d", wantExecute, len(client.executeInputs))
	}
}

func newGetPolicyOutput(t *testing.T, version int32, document string, state svcsdktypes.ChangeSetState) *svcsdk.GetCoreNetworkPolicyOutput {
	t.Helper()
	policyVersion := version
	policyDocument := document
	return &svcsdk.GetCoreNetworkPolicyOutput{
		CoreNetworkPolicy: &svcsdktypes.CoreNetworkPolicy{
			PolicyVersionId: &policyVersion,
			PolicyDocument:  &policyDocument,
			ChangeSetState:  state,
		},
	}
}

func newCoreNetworkResource(t *testing.T, coreNetworkID string, policyVersionID int64, policyDocument string) *resource {
	t.Helper()
	return &resource{ko: &svcapitypes.CoreNetwork{
		Spec: svcapitypes.CoreNetworkSpec{
			PolicyDocument: aws.String(policyDocument),
		},
		Status: svcapitypes.CoreNetworkStatus{
			CoreNetworkID:   aws.String(coreNetworkID),
			PolicyVersionID: aws.Int64(policyVersionID),
		},
	}}
}

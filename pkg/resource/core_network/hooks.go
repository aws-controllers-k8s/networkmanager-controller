package core_network

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"

	"github.com/aws-controllers-k8s/networkmanager-controller/pkg/tags"
	"github.com/aws-controllers-k8s/runtime/pkg/requeue"
	"github.com/aws/aws-sdk-go-v2/aws"
	svcsdk "github.com/aws/aws-sdk-go-v2/service/networkmanager"
	svcsdktypes "github.com/aws/aws-sdk-go-v2/service/networkmanager/types"
	smithy "github.com/aws/smithy-go"
)

var syncTags = tags.SyncTags

type coreNetworkPolicyClient interface {
	GetCoreNetworkPolicy(context.Context, *svcsdk.GetCoreNetworkPolicyInput, ...func(*svcsdk.Options)) (*svcsdk.GetCoreNetworkPolicyOutput, error)
	PutCoreNetworkPolicy(context.Context, *svcsdk.PutCoreNetworkPolicyInput, ...func(*svcsdk.Options)) (*svcsdk.PutCoreNetworkPolicyOutput, error)
	ExecuteCoreNetworkChangeSet(context.Context, *svcsdk.ExecuteCoreNetworkChangeSetInput, ...func(*svcsdk.Options)) (*svcsdk.ExecuteCoreNetworkChangeSetOutput, error)
}

var (
	requeueWaitWhileDeleting = requeue.NeededAfter(
		errors.New(GroupKind.Kind+" is deleting"),
		requeue.DefaultRequeueAfterDuration,
	)
	requeueWaitWhilePolicyDocumentUpdating = requeue.NeededAfter(
		errors.New(GroupKind.Kind+" policy document is updating"),
		requeue.DefaultRequeueAfterDuration,
	)
	requeueWaitWhilePolicyDocumentExecuting = requeue.NeededAfter(
		errors.New(GroupKind.Kind+" policy document is executing"),
		requeue.DefaultRequeueAfterDuration,
	)
	requeueWaitWhilePolicyDocumentGenerating = requeue.NeededAfter(
		errors.New(GroupKind.Kind+" policy document changeset is pending generation"),
		requeue.DefaultRequeueAfterDuration,
	)
)

func syncPolicyDocument(
	ctx context.Context,
	client coreNetworkPolicyClient,
	desired *resource,
	live *resource,
) error {
	fmt.Println("IN SYNC POLICY")
	coreNetworkID := live.ko.Status.CoreNetworkID

	// Check live and latest policy documents.
	// If live and latest have the same policy version ID, check their policy documents.
	// If they differ, it means the latest policy document has been updated
	// since the last time we checked, and we should attempt to submit it for execution.
	// If live and latest have different policy version IDs, it means a new policy document
	// has been submitted and is either pending generation, ready to execute, or executing.
	latestPolicyResp, err := client.GetCoreNetworkPolicy(ctx, &svcsdk.GetCoreNetworkPolicyInput{
		CoreNetworkId: coreNetworkID,
		Alias:         svcsdktypes.CoreNetworkPolicyAliasLatest,
	})
	// If there are no policies at all, GetCoreNetworkPolicy will throw a ValidationException.
	// We can ignore this error and treat it as if there is no latest policy.
	if ignoreValidationException(err) != nil {
		return err
	}

	livePolicyVersionId := int32(0)
	if live.ko.Status.PolicyVersionID != nil {
		livePolicyVersionId = (int32)(*live.ko.Status.PolicyVersionID)
	}

	latestPolicyVersionId := int32(0)
	var latestPolicyDocument *string
	latestPolicyChangeSetState := svcsdktypes.ChangeSetState("")
	if latestPolicyResp != nil && latestPolicyResp.CoreNetworkPolicy != nil {
		latestPolicyVersionId = *latestPolicyResp.CoreNetworkPolicy.PolicyVersionId
		latestPolicyDocument = latestPolicyResp.CoreNetworkPolicy.PolicyDocument
		latestPolicyChangeSetState = latestPolicyResp.CoreNetworkPolicy.ChangeSetState
	}

	var desiredPolicyDocument *string
	if desired.ko.Spec.PolicyDocument != nil {
		desiredPolicyDocument = desired.ko.Spec.PolicyDocument
	}
	if livePolicyVersionId == latestPolicyVersionId {
		if !arePolicyDocumentsEqual(desiredPolicyDocument, latestPolicyDocument) {
			fmt.Printf("ABOUT TO PUTCORENETWORKPOLICY: DESIRED - %#v\n", desiredPolicyDocument)
			putInput := &svcsdk.PutCoreNetworkPolicyInput{
				CoreNetworkId:  coreNetworkID,
				PolicyDocument: desiredPolicyDocument,
			}
			_, err := client.PutCoreNetworkPolicy(ctx, putInput)
			if err != nil {
				return err
			}
			// Requeue immediately to start polling for READY_TO_EXECUTE status
			return requeueWaitWhilePolicyDocumentUpdating
		}
		// If we are here it means the live policy is the same as the desired policy, nothing to do
		return nil
	}

	switch latestPolicyChangeSetState {
	case svcsdktypes.ChangeSetStatePendingGeneration:
		return requeueWaitWhilePolicyDocumentGenerating

	case svcsdktypes.ChangeSetStateReadyToExecute:
		_, err := client.ExecuteCoreNetworkChangeSet(ctx, &svcsdk.ExecuteCoreNetworkChangeSetInput{
			CoreNetworkId:   coreNetworkID,
			PolicyVersionId: aws.Int32(latestPolicyVersionId),
		})
		if err != nil {
			return err
		}
		return requeueWaitWhilePolicyDocumentExecuting

	case svcsdktypes.ChangeSetStateExecuting:
		return requeueWaitWhilePolicyDocumentExecuting
	}
	return nil
}

// arePolicyDocumentsEqual performs a semantic JSON comparison of the two provided policy documents.
// If either document is not valid JSON, it falls back to a string comparison. Nil and empty are considered equal.
func arePolicyDocumentsEqual(a, b *string) bool {
	var jsonA, jsonB any
	sA := normalizePolicyDocument(a)
	sB := normalizePolicyDocument(b)
	errA := json.Unmarshal([]byte(sA), &jsonA)
	errB := json.Unmarshal([]byte(sB), &jsonB)

	if errA != nil || errB != nil {
		return sA == sB
	}

	return reflect.DeepEqual(jsonA, jsonB)
}

func normalizePolicyDocument(policyDocument *string) string {
	if policyDocument == nil {
		return ""
	}
	return *policyDocument
}

func ignoreValidationException(err error) error {
	if err != nil {
		var awsErr smithy.APIError
		if !errors.As(err, &awsErr) || awsErr.ErrorCode() != "ValidationException" {
			return err
		}
	}
	return nil
}

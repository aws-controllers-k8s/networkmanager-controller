package core_network

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"

	"github.com/aws-controllers-k8s/networkmanager-controller/pkg/tags"
	"github.com/aws-controllers-k8s/runtime/pkg/requeue"
	svcsdk "github.com/aws/aws-sdk-go-v2/service/networkmanager"
	svcsdktypes "github.com/aws/aws-sdk-go-v2/service/networkmanager/types"
)

var syncTags = tags.SyncTags

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

func (rm *resourceManager) syncPolicyDocument(
	ctx context.Context,
	desired *resource,
	latest *resource,
) error {
	coreNetworkID := latest.ko.Status.CoreNetworkID

	// Check live and latest policy documents.
	// If live and latest have the same policy version ID, check their policy documents.
	// If they differ, it means the latest policy document has been updated
	// since the last time we checked, and we should attempt to submit it for execution.
	// If live and latest have different policy version IDs, it means a new policy document
	// has been submitted and is either pending generation, ready to execute, or executing.
	latestPolicyResp, err := rm.sdkapi.GetCoreNetworkPolicy(ctx, &svcsdk.GetCoreNetworkPolicyInput{
		CoreNetworkId: coreNetworkID,
		Alias:         svcsdktypes.CoreNetworkPolicyAliasLatest,
	})
	if err != nil {
		return err
	}

	livePolicyResp, err := rm.sdkapi.GetCoreNetworkPolicy(ctx, &svcsdk.GetCoreNetworkPolicyInput{
		CoreNetworkId: coreNetworkID,
		Alias:         svcsdktypes.CoreNetworkPolicyAliasLive,
	})
	if err != nil {
		return err
	}
	if *livePolicyResp.CoreNetworkPolicy.PolicyVersionId == *latestPolicyResp.CoreNetworkPolicy.PolicyVersionId {
		if !arePolicyDocumentsEqual(*desired.ko.Spec.PolicyDocument, *latestPolicyResp.CoreNetworkPolicy.PolicyDocument) {
			putInput := &svcsdk.PutCoreNetworkPolicyInput{
				CoreNetworkId:  coreNetworkID,
				PolicyDocument: desired.ko.Spec.PolicyDocument,
			}
			_, err := rm.sdkapi.PutCoreNetworkPolicy(ctx, putInput)
			if err != nil {
				return err
			}
			// Requeue immediately to start polling for READY_TO_EXECUTE status
			return requeueWaitWhilePolicyDocumentUpdating
		}
		return nil
	}

	changeSetState := latestPolicyResp.CoreNetworkPolicy.ChangeSetState
	policyVersionId := latestPolicyResp.CoreNetworkPolicy.PolicyVersionId

	switch changeSetState {
	case svcsdktypes.ChangeSetStatePendingGeneration:
		return requeueWaitWhilePolicyDocumentGenerating

	case svcsdktypes.ChangeSetStateReadyToExecute:
		_, err := rm.sdkapi.ExecuteCoreNetworkChangeSet(ctx, &svcsdk.ExecuteCoreNetworkChangeSetInput{
			CoreNetworkId:   coreNetworkID,
			PolicyVersionId: policyVersionId,
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
// If either document is not valid JSON, it falls back to a string comparison
func arePolicyDocumentsEqual(a, b string) bool {
	var jsonA, jsonB any
	errA := json.Unmarshal([]byte(a), &jsonA)
	errB := json.Unmarshal([]byte(b), &jsonB)

	if errA != nil || errB != nil {
		return a == b
	}

	return reflect.DeepEqual(jsonA, jsonB)
}

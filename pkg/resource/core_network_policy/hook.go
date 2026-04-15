package core_network_policy

import (
	"context"
	"fmt"
	"time"

	svcapitypes "github.com/aws-controllers-k8s/networkmanager-controller/apis/v1alpha1"
	ackcompare "github.com/aws-controllers-k8s/runtime/pkg/compare"
	ackcondition "github.com/aws-controllers-k8s/runtime/pkg/condition"
	ackrequeue "github.com/aws-controllers-k8s/runtime/pkg/requeue"
	"github.com/aws/aws-sdk-go-v2/aws"
	svcsdk "github.com/aws/aws-sdk-go-v2/service/networkmanager"
	corev1 "k8s.io/api/core/v1"
)

const (
	changeSetPollInterval = 15 * time.Second
)

// customUpdateCoreNetworkPolicy updates a core network policy by calling
// PutCoreNetworkPolicy, then orchestrates the change-set workflow.
func (rm *resourceManager) customUpdateCoreNetworkPolicy(
	ctx context.Context,
	desired *resource,
	latest *resource,
	delta *ackcompare.Delta,
) (*resource, error) {
	input := &svcsdk.PutCoreNetworkPolicyInput{
		CoreNetworkId:  desired.ko.Spec.CoreNetworkID,
		PolicyDocument: desired.ko.Spec.PolicyDocument,
	}
	if desired.ko.Spec.Description != nil {
		input.Description = desired.ko.Spec.Description
	}

	resp, err := rm.sdkapi.PutCoreNetworkPolicy(ctx, input)
	rm.metrics.RecordAPICall("UPDATE", "PutCoreNetworkPolicy", err)
	if err != nil {
		return nil, err
	}

	ko := desired.ko.DeepCopy()
	if resp.CoreNetworkPolicy != nil {
		if resp.CoreNetworkPolicy.PolicyVersionId != nil {
			pv := int64(*resp.CoreNetworkPolicy.PolicyVersionId)
			ko.Status.PolicyVersionID = &pv
		}
		if resp.CoreNetworkPolicy.Alias != "" {
			ko.Status.Alias = aws.String(string(resp.CoreNetworkPolicy.Alias))
		}
		if resp.CoreNetworkPolicy.ChangeSetState != "" {
			ko.Status.ChangeSetState = aws.String(string(resp.CoreNetworkPolicy.ChangeSetState))
		}
		if resp.CoreNetworkPolicy.PolicyErrors != nil {
			policyErrors := []*svcapitypes.CoreNetworkPolicyError{}
			for _, pe := range resp.CoreNetworkPolicy.PolicyErrors {
				policyErrors = append(policyErrors, &svcapitypes.CoreNetworkPolicyError{
					ErrorCode: pe.ErrorCode,
					Message:   pe.Message,
					Path:      pe.Path,
				})
			}
			ko.Status.PolicyErrors = policyErrors
		} else {
			ko.Status.PolicyErrors = nil
		}
	}

	if err := rm.handleChangeSetWorkflow(ctx, ko); err != nil {
		return &resource{ko}, err
	}

	return &resource{ko}, nil
}

// handleChangeSetWorkflow retrieves the change set for the latest policy version
// and drives the state machine: get change set -> optionally execute -> poll.
func (rm *resourceManager) handleChangeSetWorkflow(
	ctx context.Context,
	ko *svcapitypes.CoreNetworkPolicy,
) error {
	if ko.Status.PolicyVersionID == nil {
		return nil
	}

	pv := int32(*ko.Status.PolicyVersionID)

	csOutput, err := rm.sdkapi.GetCoreNetworkChangeSet(ctx, &svcsdk.GetCoreNetworkChangeSetInput{
		CoreNetworkId:   ko.Spec.CoreNetworkID,
		PolicyVersionId: &pv,
	})
	rm.metrics.RecordAPICall("READ_ONE", "GetCoreNetworkChangeSet", err)
	if err != nil {
		ackcondition.SetSynced(
			&resource{ko}, corev1.ConditionFalse,
			aws.String(err.Error()), aws.String("ChangeSetFailed"),
		)
		return ackrequeue.NeededAfter(
			fmt.Errorf("failed to get change set: %w", err),
			changeSetPollInterval,
		)
	}

	// Clear previous policy errors on successful change-set retrieval
	ko.Status.PolicyErrors = nil

	if len(csOutput.CoreNetworkChanges) > 0 {
		ko.Status.ChangeSetState = aws.String("READY_TO_EXECUTE")
	}

	return rm.evaluateChangeSetState(ctx, ko)
}

// evaluateChangeSetState processes the current ChangeSetState and takes appropriate action.
func (rm *resourceManager) evaluateChangeSetState(
	ctx context.Context,
	ko *svcapitypes.CoreNetworkPolicy,
) error {
	state := aws.ToString(ko.Status.ChangeSetState)

	switch state {
	case "READY_TO_EXECUTE":
		autoExec := ko.Spec.AutoExecute != nil && *ko.Spec.AutoExecute
		if autoExec {
			return rm.executeChangeSet(ctx, ko)
		}
		ackcondition.SetSynced(
			&resource{ko}, corev1.ConditionFalse,
			aws.String("Change set ready, waiting for manual execution"),
			aws.String("PendingExecution"),
		)
		return ackrequeue.NeededAfter(nil, changeSetPollInterval)

	case "EXECUTING":
		ackcondition.SetSynced(
			&resource{ko}, corev1.ConditionFalse,
			aws.String("Change set is being executed"),
			aws.String("Executing"),
		)
		return ackrequeue.NeededAfter(nil, changeSetPollInterval)

	case "EXECUTION_SUCCEEDED", "EXECUTED":
		ackcondition.SetSynced(&resource{ko}, corev1.ConditionTrue, nil, nil)
		return nil

	case "FAILED_GENERATION", "FAILED_EXECUTION":
		ackcondition.SetSynced(
			&resource{ko}, corev1.ConditionFalse,
			aws.String(fmt.Sprintf("Change set %s", state)),
			aws.String("Failed"),
		)
		return nil

	default:
		ackcondition.SetSynced(
			&resource{ko}, corev1.ConditionFalse,
			aws.String(fmt.Sprintf("Change set state: %s", state)),
			aws.String("Processing"),
		)
		return ackrequeue.NeededAfter(nil, changeSetPollInterval)
	}
}

// executeChangeSet calls ExecuteCoreNetworkChangeSet and handles the result.
func (rm *resourceManager) executeChangeSet(
	ctx context.Context,
	ko *svcapitypes.CoreNetworkPolicy,
) error {
	pv := int32(*ko.Status.PolicyVersionID)
	_, err := rm.sdkapi.ExecuteCoreNetworkChangeSet(ctx, &svcsdk.ExecuteCoreNetworkChangeSetInput{
		CoreNetworkId:   ko.Spec.CoreNetworkID,
		PolicyVersionId: &pv,
	})
	rm.metrics.RecordAPICall("UPDATE", "ExecuteCoreNetworkChangeSet", err)
	if err != nil {
		ko.Status.ChangeSetState = aws.String("FAILED_EXECUTION")
		ackcondition.SetSynced(
			&resource{ko}, corev1.ConditionFalse,
			aws.String(err.Error()), aws.String("ExecuteFailed"),
		)
		return err
	}

	ko.Status.ChangeSetState = aws.String("EXECUTING")
	ackcondition.SetSynced(
		&resource{ko}, corev1.ConditionFalse,
		aws.String("Executing policy change set"),
		aws.String("Executing"),
	)
	return ackrequeue.NeededAfter(nil, changeSetPollInterval)
}

// checkChangeSetStateOnRead is called from the sdk_read_one_post_set_output hook.
// It continues the state machine during reconcile reads for transitional states.
func (rm *resourceManager) checkChangeSetStateOnRead(
	ctx context.Context,
	ko *svcapitypes.CoreNetworkPolicy,
) error {
	state := aws.ToString(ko.Status.ChangeSetState)
	switch state {
	case "", "EXECUTION_SUCCEEDED", "EXECUTED":
		return nil
	case "READY_TO_EXECUTE":
		autoExec := ko.Spec.AutoExecute != nil && *ko.Spec.AutoExecute
		if autoExec {
			return rm.executeChangeSet(ctx, ko)
		}
		ackcondition.SetSynced(
			&resource{ko}, corev1.ConditionFalse,
			aws.String("Change set ready, waiting for manual execution"),
			aws.String("PendingExecution"),
		)
		return ackrequeue.NeededAfter(nil, changeSetPollInterval)
	case "EXECUTING":
		ackcondition.SetSynced(
			&resource{ko}, corev1.ConditionFalse,
			aws.String("Change set is being executed"),
			aws.String("Executing"),
		)
		return ackrequeue.NeededAfter(nil, changeSetPollInterval)
	default:
		return nil
	}
}

// Reference to avoid unused import error
var _ = &svcapitypes.CoreNetworkPolicy{}
var _ *ackcompare.Delta

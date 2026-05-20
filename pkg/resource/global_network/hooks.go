package global_network

import (
	"errors"

	"github.com/aws-controllers-k8s/networkmanager-controller/pkg/tags"
	"github.com/aws-controllers-k8s/runtime/pkg/requeue"
)

var syncTags = tags.SyncTags
var requeueWaitWhileDeleting = requeue.NeededAfter(
	errors.New(GroupKind.Kind+" is deleting."),
	requeue.DefaultRequeueAfterDuration,
)

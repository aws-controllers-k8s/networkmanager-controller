if ackcompare.HasNilDifference(a.ko.Spec.PolicyDocument, b.ko.Spec.PolicyDocument) {
	delta.Add("Spec.PolicyDocument", a.ko.Spec.PolicyDocument, b.ko.Spec.PolicyDocument)
} else if a.ko.Spec.PolicyDocument != nil && b.ko.Spec.PolicyDocument != nil {
	if !arePolicyDocumentsEqual(*a.ko.Spec.PolicyDocument, *b.ko.Spec.PolicyDocument) {
		delta.Add("Spec.PolicyDocument", a.ko.Spec.PolicyDocument, b.ko.Spec.PolicyDocument)
	}
}
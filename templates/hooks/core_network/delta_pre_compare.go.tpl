if !arePolicyDocumentsEqual(a.ko.Spec.PolicyDocument, b.ko.Spec.PolicyDocument) {
	delta.Add("Spec.PolicyDocument", a.ko.Spec.PolicyDocument, b.ko.Spec.PolicyDocument)
}
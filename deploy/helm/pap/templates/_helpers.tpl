{{- define "pap.labels" -}}
app.kubernetes.io/part-of: pap
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}
{{- define "pap.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}

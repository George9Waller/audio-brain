{{- define "audio-brain.labels" -}}
app.kubernetes.io/name: {{ .Values.appName }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{- define "audio-brain.image" -}}
{{ .Values.image.repository }}:{{ .Values.env.IMAGE_TAG }}
{{- end }}

{{/* The environment every server container gets: settings, secrets and where Postgres is */}}
{{- define "audio-brain.env" -}}
envFrom:
  - configMapRef:
      name: {{ .Values.appName }}-config
  - secretRef:
      name: {{ .Values.appName }}-aws-secrets
{{- end }}

{{/* The pod-level parts shared by the API and the worker */}}
{{- define "audio-brain.podSpec" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
securityContext:
  fsGroup: 1000  # the image's app user, so the data volume is writable
nodeSelector:
  kubernetes.io/arch: amd64  # essentia-tensorflow has no arm64 Linux build
volumes:
  - name: data
    persistentVolumeClaim:
      claimName: {{ .Values.appName }}-data
{{- end }}

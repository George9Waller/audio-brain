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

{{/* Which node a pod runs on: (dict "root" $ "node" "large"|"small"), from .Values.nodes */}}
{{- define "audio-brain.placement" -}}
{{- $node := index .root.Values.nodes .node }}
nodeSelector:
  kubernetes.io/arch: amd64  # essentia-tensorflow has no arm64 Linux build
  {{- toYaml $node.nodeSelector | nindent 2 }}
{{- with $node.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{/* The pod-level parts shared by the API and the workers: (dict "root" $ "node" <node> "data" <mount the data PVC>) */}}
{{- define "audio-brain.podSpec" -}}
{{- with .root.Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
securityContext:
  fsGroup: 1000  # the image's app user, so the data volume is writable
{{ include "audio-brain.placement" . }}
{{- if .data }}
volumes:
  - name: data
    persistentVolumeClaim:
      claimName: {{ .root.Values.appName }}-{{ .root.Values.data.claim }}
{{- end }}
{{- end }}

{{/* Thread caps for numpy, scikit-learn, torch and TensorFlow, so one pod's training can't take every core */}}
{{- define "audio-brain.threads" -}}
- name: OMP_NUM_THREADS
  value: {{ . | quote }}
- name: MKL_NUM_THREADS
  value: {{ . | quote }}
- name: OPENBLAS_NUM_THREADS
  value: {{ . | quote }}
- name: TF_NUM_INTRAOP_THREADS
  value: {{ . | quote }}
{{- end }}

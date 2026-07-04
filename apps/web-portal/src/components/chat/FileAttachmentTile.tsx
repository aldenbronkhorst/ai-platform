import { useEffect, useMemo } from "react";
import { AlertCircle, FileArchive, FileCode, FileImage, FileSpreadsheet, FileText, RefreshCw, X } from "lucide-react";

import type { AttachedFile, ChatAttachment } from "../../types";
import { cn } from "../../lib/utils";
import { Tip } from "../ui/tooltip";

type TileSource = ChatAttachment | AttachedFile;

interface FileAttachmentTileProps {
  attachment: TileSource;
  onOpen?: (attachment: ChatAttachment) => void;
  onRemove?: (id: string) => void;
  variant?: "composer" | "message";
}

function sourceFilename(source: TileSource) {
  return "file" in source ? source.file.name : source.filename;
}

function sourceMimeType(source: TileSource) {
  return "file" in source ? source.file.type || source.artifact?.mime_type || "application/octet-stream" : source.mime_type;
}

function sourceArtifact(source: TileSource): ChatAttachment | null {
  if ("file" in source) {
    return source.artifact ?? (source.id && !source.uploading && !source.error ? {
      id: source.id,
      artifact_type: "chat-upload",
      filename: source.file.name,
      mime_type: sourceMimeType(source),
    } : null);
  }
  return source;
}

function sourceArtifactType(source: TileSource) {
  return "file" in source ? source.artifact?.artifact_type : source.artifact_type;
}

function sourceUploading(source: TileSource) {
  return "file" in source ? source.uploading : false;
}

function sourceError(source: TileSource) {
  return "file" in source ? source.error : undefined;
}

function extension(filename: string) {
  const clean = filename.split(/[?#]/, 1)[0] || filename;
  const dot = clean.lastIndexOf(".");
  return dot > -1 ? clean.slice(dot + 1).trim().toLowerCase() : "";
}

function typeLabel(filename: string, mimeType: string) {
  const ext = extension(filename);
  if (ext) return ext.slice(0, 8).toUpperCase();
  if (mimeType.startsWith("image/")) return "IMAGE";
  if (mimeType.includes("pdf")) return "PDF";
  if (mimeType.includes("spreadsheet") || mimeType.includes("excel")) return "XLSX";
  if (mimeType.includes("csv")) return "CSV";
  if (mimeType.startsWith("text/")) return "TXT";
  return "FILE";
}

function iconKind(filename: string, mimeType: string): "image" | "spreadsheet" | "archive" | "code" | "text" {
  const ext = extension(filename);
  if (mimeType.startsWith("image/")) return "image";
  if (mimeType.includes("spreadsheet") || mimeType.includes("excel") || ["csv", "tsv", "xls", "xlsx"].includes(ext)) return "spreadsheet";
  if (["zip", "rar", "7z", "gz"].includes(ext)) return "archive";
  if (["js", "ts", "tsx", "jsx", "py", "json", "xml", "html", "css", "sql", "md"].includes(ext)) return "code";
  return "text";
}

function FileTypeIcon({ filename, mimeType }: { filename: string; mimeType: string }) {
  const kind = iconKind(filename, mimeType);
  if (kind === "image") return <FileImage className="h-4 w-4" />;
  if (kind === "spreadsheet") return <FileSpreadsheet className="h-4 w-4" />;
  if (kind === "archive") return <FileArchive className="h-4 w-4" />;
  if (kind === "code") return <FileCode className="h-4 w-4" />;
  return <FileText className="h-4 w-4" />;
}

function useLocalImagePreview(source: TileSource, mimeType: string) {
  const file = "file" in source ? source.file : null;
  const url = useMemo(() => {
    if (!file || !mimeType.startsWith("image/")) return null;
    return URL.createObjectURL(file);
  }, [file, mimeType]);

  useEffect(() => () => {
    if (url) URL.revokeObjectURL(url);
  }, [url]);

  return url;
}

export function FileAttachmentTile({
  attachment,
  onOpen,
  onRemove,
  variant = "message",
}: FileAttachmentTileProps) {
  const id = attachment.id;
  const filename = sourceFilename(attachment);
  const mimeType = sourceMimeType(attachment);
  const artifactType = sourceArtifactType(attachment);
  const artifact = sourceArtifact(attachment);
  const uploading = sourceUploading(attachment);
  const error = sourceError(attachment);
  const previewUrl = useLocalImagePreview(attachment, mimeType);
  const fileType = typeLabel(filename, mimeType);
  const detail = error || (uploading ? "Uploading" : artifactType === "chat-generated" ? `Generated ${fileType}` : fileType);
  const interactive = Boolean(artifact && onOpen && !uploading && !error);
  const tooltip = (
    <span className="file-attachment-tip">
      <span className="file-attachment-tip-name">{filename}</span>
      <span className="file-attachment-tip-detail">{detail}</span>
    </span>
  );

  const handleOpen = () => {
    if (artifact && onOpen && !uploading && !error) onOpen(artifact);
  };

  const tile = (
    <button
      aria-busy={uploading || undefined}
      aria-disabled={!interactive || undefined}
      aria-label={interactive ? `Open ${filename}` : filename}
      className={cn(
        "file-attachment-tile",
        error && "file-attachment-tile-error",
        artifactType === "chat-generated" && "file-attachment-tile-generated",
        interactive && "file-attachment-tile-interactive",
      )}
      onClick={(event) => {
        event.stopPropagation();
        handleOpen();
      }}
      tabIndex={interactive ? 0 : -1}
      type="button"
    >
      <span className="file-attachment-thumb">
        {previewUrl ? (
          <img alt="" className="file-attachment-thumb-image" draggable={false} src={previewUrl} />
        ) : error ? (
          <AlertCircle className="h-4 w-4" />
        ) : (
          <FileTypeIcon filename={filename} mimeType={mimeType} />
        )}
        {uploading && (
          <span className="file-attachment-uploading">
            <RefreshCw className="h-3.5 w-3.5 animate-spin" />
          </span>
        )}
      </span>
      <span className="file-attachment-copy">
        <span className="file-attachment-name">{filename}</span>
        <span className={cn("file-attachment-type", error && "file-attachment-type-error")}>{detail}</span>
      </span>
    </button>
  );

  return (
    <div
      className={cn("file-attachment-tile-wrap", variant === "composer" && "file-attachment-tile-wrap-composer")}
      data-slot="file-attachment"
      data-variant={variant}
    >
      <Tip label={tooltip} side="top">
        {tile}
      </Tip>
      {onRemove && id && !uploading && (
        <button
          aria-label={`Remove ${filename}`}
          className="file-attachment-remove"
          onClick={(event) => {
            event.stopPropagation();
            onRemove(id);
          }}
          type="button"
        >
          <X className="h-3 w-3" />
        </button>
      )}
    </div>
  );
}

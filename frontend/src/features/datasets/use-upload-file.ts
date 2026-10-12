import { useRef, useState } from "react";

import { MAX_UPLOAD_BYTES, previewUpload, uploadKind, type UploadPreview } from "./upload-preview";

export interface FileProblem {
  title: string;
  detail: string;
}

export interface ChosenFile {
  file: File;
  /** Null while the file is read, or when it has a problem. */
  preview: UploadPreview | null;
  problem: FileProblem | null;
}

/**
 * The file picked for an upload and what the browser could read of it: the preview of the first
 * rows, or why the server would refuse the whole file (type, size, encoding, a CSV header).
 */
export function useUploadFile() {
  const [chosen, setChosen] = useState<ChosenFile | null>(null);
  // The latest pick: a read that finishes after another pick (or a clear) is ignored.
  const current = useRef<File | null>(null);

  async function choose(file: File) {
    current.current = file;
    const kind = uploadKind(file.name);
    if (kind === null) {
      setChosen({ file, preview: null, problem: unsupported(file.name) });
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setChosen({ file, preview: null, problem: TOO_LARGE });
      return;
    }
    setChosen({ file, preview: null, problem: null });
    const text = await readUtf8(file);
    if (current.current !== file) return;
    if (text === null) {
      setChosen({ file, preview: null, problem: NOT_UTF8 });
      return;
    }
    const preview = previewUpload(kind, text);
    setChosen({
      file,
      preview,
      problem: preview.fileProblem
        ? { title: "This file can't be read as a dataset", detail: preview.fileProblem }
        : null,
    });
  }

  return {
    chosen,
    choose: (file: File) => {
      void choose(file);
    },
    clear: () => {
      current.current = null;
      setChosen(null);
    },
  };
}

async function readUtf8(file: File): Promise<string | null> {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
  } catch {
    return null;
  }
}

function unsupported(name: string): FileProblem {
  return {
    title: "This file type is not supported",
    detail: `Only .jsonl and .csv files can be uploaded. Convert “${name}” to CSV and try again.`,
  };
}

const TOO_LARGE: FileProblem = {
  title: "This file is larger than 5 MB",
  detail: "Split it into smaller files and upload each one.",
};

const NOT_UTF8: FileProblem = {
  title: "This file is not UTF-8 text",
  detail: "Save it with UTF-8 encoding and try again.",
};

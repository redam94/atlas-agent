import { useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import {
  useStartMarkdownIngest,
  useStartPdfIngest,
  useIngestJob,
  type IngestionJob,
} from "@/hooks/use-ingest-job";

export function IngestModal(props: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project_id: string;
}) {
  const [tab, setTab] = useState<"markdown" | "pdf">("markdown");
  const [markdown, setMarkdown] = useState("");
  const [filename, setFilename] = useState("");
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [activeJob, setActiveJob] = useState<IngestionJob | null>(null);

  const startMd = useStartMarkdownIngest();
  const startPdf = useStartPdfIngest();
  const polled = useIngestJob(activeJob?.id);
  const job = polled.data ?? activeJob;

  const submit = async () => {
    if (tab === "markdown") {
      if (!markdown.trim()) return;
      const j = await startMd.mutateAsync({
        project_id: props.project_id,
        text: markdown,
        source_filename: filename.trim() || undefined,
      });
      setActiveJob(j);
    } else {
      if (!pdfFile) return;
      const j = await startPdf.mutateAsync({ project_id: props.project_id, file: pdfFile });
      setActiveJob(j);
    }
  };

  const reset = () => {
    setActiveJob(null);
    setMarkdown("");
    setFilename("");
    setPdfFile(null);
    setTab("markdown");
  };

  const close = () => {
    reset();
    props.onOpenChange(false);
  };

  return (
    <Dialog
      open={props.open}
      onOpenChange={(o) => {
        if (!o) reset();
        props.onOpenChange(o);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add knowledge</DialogTitle>
          <DialogDescription>Ingest markdown or PDF documents into your project knowledge base</DialogDescription>
        </DialogHeader>

        {!job && (
          <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
            <TabsList>
              <TabsTrigger value="markdown">Markdown</TabsTrigger>
              <TabsTrigger value="pdf">PDF</TabsTrigger>
            </TabsList>
            <TabsContent value="markdown">
              <div className="space-y-3">
                <div className="space-y-1.5">
                  <Label htmlFor="md-title">Title (optional)</Label>
                  <Input
                    id="md-title"
                    value={filename}
                    onChange={(e) => setFilename(e.target.value)}
                    placeholder="Geo-lift design notes"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="md-text">Markdown</Label>
                  <Textarea
                    id="md-text"
                    value={markdown}
                    onChange={(e) => setMarkdown(e.target.value)}
                    rows={10}
                  />
                </div>
              </div>
            </TabsContent>
            <TabsContent value="pdf">
              <div className="space-y-3">
                <Label htmlFor="pdf-file">PDF file</Label>
                <Input
                  id="pdf-file"
                  type="file"
                  accept="application/pdf"
                  onChange={(e) => setPdfFile(e.target.files?.[0] ?? null)}
                />
              </div>
            </TabsContent>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost" onClick={() => props.onOpenChange(false)}>Cancel</Button>
              <Button onClick={submit} disabled={startMd.isPending || startPdf.isPending}>
                Ingest
              </Button>
            </div>
          </Tabs>
        )}

        {job && job.status !== "completed" && job.status !== "failed" && (
          <div className="space-y-2 py-4 text-sm">
            <div>Ingesting… ({job.status})</div>
            <div className="text-muted-foreground text-xs">
              You can close this modal — the job continues in the background.
            </div>
            <Button variant="ghost" onClick={close}>Close</Button>
          </div>
        )}
        {job?.status === "completed" && (
          <div className="space-y-3 py-4">
            <div className="text-sm">Ingested {job.node_ids.length} chunks.</div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={reset}>Add another</Button>
              <Button onClick={close}>Done</Button>
            </div>
          </div>
        )}
        {job?.status === "failed" && (
          <div className="space-y-3 py-4">
            <div className="text-sm text-destructive">{job.error ?? "Ingestion failed."}</div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={close}>Cancel</Button>
              <Button onClick={reset}>Retry</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

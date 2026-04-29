import { useEffect } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchKnowledgeGraph,
  KnowledgeGraphError,
  type GraphResponse,
} from "@/lib/api/knowledge-graph";
import { useExplorerStore } from "@/stores/explorer-store";
import { ExplorerCanvas } from "@/components/explorer/explorer-canvas";
import { ExplorerEmptyState } from "@/components/explorer/explorer-empty-state";
import { ExplorerFilterPills } from "@/components/explorer/explorer-filter-pills";
import { ExplorerSearchBar } from "@/components/explorer/explorer-search-bar";
import { ExplorerSidePanel } from "@/components/explorer/explorer-side-panel";

export function ExplorerRoute() {
  const { id: projectId } = useParams<{ id: string }>();
  const replaceGraph = useExplorerStore((s) => s.replaceGraph);
  const mergeGraph = useExplorerStore((s) => s.mergeGraph);
  const reset = useExplorerStore((s) => s.reset);
  const truncated = useExplorerStore((s) => s.truncated);
  const degradedStages = useExplorerStore((s) => s.degradedStages);
  const nodes = useExplorerStore((s) => s.nodes);
  const queryClient = useQueryClient();

  // Reset store when project changes.
  useEffect(() => {
    reset();
  }, [projectId, reset]);

  const overviewQuery = useQuery({
    queryKey: ["graph", projectId, "overview"],
    enabled: !!projectId,
    staleTime: 30_000,
    queryFn: ({ signal }) =>
      fetchKnowledgeGraph({ projectId: projectId!, limit: 30 }, signal),
  });

  useEffect(() => {
    if (overviewQuery.data) replaceGraph(overviewQuery.data);
  }, [overviewQuery.data, replaceGraph]);

  const searchMutation = useMutation({
    mutationFn: (q: string) =>
      fetchKnowledgeGraph({ projectId: projectId!, q, limit: 50 }),
    onSuccess: (data) => replaceGraph(data),
  });

  const expandMutation = useMutation({
    mutationFn: (seedId: string) =>
      fetchKnowledgeGraph({
        projectId: projectId!,
        seedNodeIds: [seedId],
        limit: 50,
      }),
    onSuccess: (data: GraphResponse) => mergeGraph(data),
  });

  if (!projectId) return null;

  const isLoading = overviewQuery.isPending || searchMutation.isPending;
  const error = overviewQuery.error || searchMutation.error || expandMutation.error;
  const errorMessage =
    error instanceof KnowledgeGraphError ? error.message : (error as Error)?.message;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b p-3">
        <ExplorerSearchBar
          onSubmit={(q) => searchMutation.mutate(q)}
          onClear={() => {
            queryClient.invalidateQueries({ queryKey: ["graph", projectId, "overview"] });
            reset();
          }}
        />
        <ExplorerFilterPills />
      </header>
      {truncated && (
        <div className="border-b bg-amber-50 px-3 py-1 text-xs text-amber-900">
          Showing top results — refine your search to see more.
        </div>
      )}
      {degradedStages.includes("graph_unavailable") && (
        <div className="border-b bg-amber-50 px-3 py-1 text-xs text-amber-900">
          Graph data unavailable — showing semantic results only.
        </div>
      )}
      <div className="relative flex-1 overflow-hidden">
        {isLoading && nodes.length === 0 && <ExplorerEmptyState variant="loading" />}
        {!isLoading && error && (
          <ExplorerEmptyState variant="error" message={errorMessage} />
        )}
        {!isLoading && !error && nodes.length === 0 && (
          <ExplorerEmptyState variant="empty" />
        )}
        {nodes.length > 0 && <ExplorerCanvas />}
        <ExplorerSidePanel onExpand={(id) => expandMutation.mutate(id)} />
      </div>
    </div>
  );
}

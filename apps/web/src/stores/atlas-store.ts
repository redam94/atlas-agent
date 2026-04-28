import { create } from "zustand";

type AtlasState = {
  auth: { user_id: "matt" };
  ui: {
    sidebar_collapsed: boolean;
    rag_drawer_open: boolean;
    rag_drawer_auto_opened_for_session: Record<string, boolean>;
  };
  models: {
    selected_id_per_session: Record<string, string>;
  };
  toggleSidebar: () => void;
  setRagDrawerOpen: (open: boolean) => void;
  markRagDrawerAutoOpened: (session_id: string) => void;
  setSelectedModel: (session_id: string, model_id: string) => void;
};

export const useAtlasStore = create<AtlasState>((set) => ({
  auth: { user_id: "matt" },
  ui: {
    sidebar_collapsed: false,
    rag_drawer_open: false,
    rag_drawer_auto_opened_for_session: {},
  },
  models: { selected_id_per_session: {} },
  toggleSidebar: () =>
    set((s) => ({ ui: { ...s.ui, sidebar_collapsed: !s.ui.sidebar_collapsed } })),
  setRagDrawerOpen: (open) =>
    set((s) => ({ ui: { ...s.ui, rag_drawer_open: open } })),
  markRagDrawerAutoOpened: (session_id) =>
    set((s) => ({
      ui: {
        ...s.ui,
        rag_drawer_auto_opened_for_session: {
          ...s.ui.rag_drawer_auto_opened_for_session,
          [session_id]: true,
        },
      },
    })),
  setSelectedModel: (session_id, model_id) =>
    set((s) => ({
      models: {
        selected_id_per_session: {
          ...s.models.selected_id_per_session,
          [session_id]: model_id,
        },
      },
    })),
}));

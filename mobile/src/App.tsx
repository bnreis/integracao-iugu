import React, { useEffect, useState } from "react";
import { ActivityIndicator, Platform, StatusBar, View } from "react-native";
import { NavigationContainer } from "@react-navigation/native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";

import LoginScreen from "./screens/LoginScreen";
import AppNavigator from "./navigation/AppNavigator";
import {
  hydrateToken,
  getEmpresaAtivaId,
  setEmpresaAtiva,
  logout,
} from "./services/api";

export default function App() {
  const [loggedIn, setLoggedIn] = useState(false);
  // Começa carregando: precisamos consultar o storage persistido antes de
  // decidir entre Login e app (no boot a memória começa vazia).
  const [hidratando, setHidratando] = useState(true);
  // Empresa ativa (ADR-0007). Serve de `key` do AppNavigator: ao trocar de
  // empresa, o navigator remonta e todas as telas recarregam com os dados dela.
  const [empresaId, setEmpresaId] = useState(getEmpresaAtivaId());

  // Troca de empresa SEM deslogar: se já houver sessão (token) na empresa
  // destino, remonta as telas com os dados dela; senão, vai pro login já
  // pré-selecionado naquela empresa.
  const trocarEmpresa = async (id: string) => {
    const temToken = await setEmpresaAtiva(id);
    // WEB: recarrega a página inteira → estado 100% limpo e requisições novas para o
    // backend da empresa escolhida (o remount por key não refazia o fetch no
    // react-native-web, mostrando os dados da empresa carregada primeiro). A empresa
    // já foi PERSISTIDA por setEmpresaAtiva, então o boot (hydrateToken) reabre nela.
    if (Platform.OS === "web" && typeof window !== "undefined") {
      window.location.reload();
      return;
    }
    // NATIVO: o key={empresaId} remonta o navigator e as telas recarregam.
    setEmpresaId(id);
    if (!temToken) setLoggedIn(false);
  };

  const sair = async () => {
    await logout();
    setLoggedIn(false);
  };

  useEffect(() => {
    // Restaura a sessão a partir do token persistido (SecureStore no nativo,
    // localStorage no web). Evita deslogar a cada reload/F5 ou reinício do app.
    hydrateToken()
      .then((temToken) => setLoggedIn(temToken))
      .finally(() => setHidratando(false));
  }, []);

  // Conteúdo conforme o estado (loading / login / app).
  let content: React.ReactNode;
  if (hidratando) {
    content = (
      <View
        style={{
          flex: 1,
          backgroundColor: "#1a56db",
          justifyContent: "center",
          alignItems: "center",
        }}
      >
        <ActivityIndicator color="#fff" size="large" />
      </View>
    );
  } else if (!loggedIn) {
    content = (
      <LoginScreen
        onLoginSuccess={() => {
          // A empresa pode ter sido trocada no seletor do login.
          setEmpresaId(getEmpresaAtivaId());
          setLoggedIn(true);
        }}
      />
    );
  } else {
    content = (
      <NavigationContainer>
        {/* key={empresaId}: trocar de empresa remonta o navigator → telas recarregam. */}
        <AppNavigator key={empresaId} onSwitch={trocarEmpresa} onLogout={sair} />
      </NavigationContainer>
    );
  }

  // GestureHandlerRootView: necessário para o swipe entre abas (gesture-handler).
  // SafeAreaProvider: expõe os insets (barra de status / barra de navegação do
  // Android) para o menu inferior não ficar atrás dos botões do celular.
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <StatusBar barStyle="light-content" backgroundColor="#1a56db" />
        {content}
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

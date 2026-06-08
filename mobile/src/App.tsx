import React, { useEffect, useState } from "react";
import { ActivityIndicator, StatusBar, View } from "react-native";
import { NavigationContainer } from "@react-navigation/native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";

import LoginScreen from "./screens/LoginScreen";
import AppNavigator from "./navigation/AppNavigator";
import { hydrateToken } from "./services/api";

export default function App() {
  const [loggedIn, setLoggedIn] = useState(false);
  // Começa carregando: precisamos consultar o storage persistido antes de
  // decidir entre Login e app (no boot a memória começa vazia).
  const [hidratando, setHidratando] = useState(true);

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
    content = <LoginScreen onLoginSuccess={() => setLoggedIn(true)} />;
  } else {
    content = (
      <NavigationContainer>
        <AppNavigator />
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

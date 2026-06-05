import React, { useEffect, useState } from "react";
import { ActivityIndicator, StatusBar, View } from "react-native";
import { NavigationContainer } from "@react-navigation/native";

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

  if (hidratando) {
    return (
      <>
        <StatusBar barStyle="light-content" backgroundColor="#1a56db" />
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
      </>
    );
  }

  if (!loggedIn) {
    return (
      <>
        <StatusBar barStyle="light-content" backgroundColor="#1a56db" />
        <LoginScreen onLoginSuccess={() => setLoggedIn(true)} />
      </>
    );
  }

  return (
    <>
      <StatusBar barStyle="light-content" backgroundColor="#1a56db" />
      <NavigationContainer>
        <AppNavigator />
      </NavigationContainer>
    </>
  );
}

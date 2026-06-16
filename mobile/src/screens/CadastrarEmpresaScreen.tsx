import React, { useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TextInput,
  TouchableOpacity,
  Switch,
  Alert,
  ActivityIndicator,
  Platform,
  KeyboardAvoidingView,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { cadastrarEmpresa, editarEmpresa } from "../services/api";

function formatCnpjInput(value: string): string {
  const digits = value.replace(/\D/g, "").slice(0, 14);
  if (digits.length <= 2) return digits;
  if (digits.length <= 5) return `${digits.slice(0, 2)}.${digits.slice(2)}`;
  if (digits.length <= 8)
    return `${digits.slice(0, 2)}.${digits.slice(2, 5)}.${digits.slice(5)}`;
  if (digits.length <= 12)
    return `${digits.slice(0, 2)}.${digits.slice(2, 5)}.${digits.slice(5, 8)}/${digits.slice(8)}`;
  return `${digits.slice(0, 2)}.${digits.slice(2, 5)}.${digits.slice(5, 8)}/${digits.slice(8, 12)}-${digits.slice(12)}`;
}

function formatCepInput(value: string): string {
  const digits = value.replace(/\D/g, "").slice(0, 8);
  if (digits.length <= 5) return digits;
  return `${digits.slice(0, 5)}-${digits.slice(5)}`;
}

export default function CadastrarEmpresaScreen({ navigation, route }: any) {
  const empresaParam = route?.params?.empresa;
  const isEditMode = !!empresaParam;

  // Dados principais
  const [cnpj, setCnpj] = useState(
    isEditMode ? formatCnpjInput(empresaParam.cnpj || "") : ""
  );
  const [razaoSocial, setRazaoSocial] = useState(
    isEditMode ? empresaParam.razao_social || "" : ""
  );
  const [email, setEmail] = useState(
    isEditMode ? empresaParam.email || "" : ""
  );

  // Cobranca
  const [descricaoBoleto, setDescricaoBoleto] = useState(
    isEditMode ? empresaParam.descricao_boleto || "" : ""
  );
  const [valorFatura, setValorFatura] = useState(
    isEditMode ? empresaParam.valor_fatura || "" : ""
  );
  const [diaCriacao, setDiaCriacao] = useState(
    isEditMode ? String(empresaParam.dia_criacao_fatura ?? "") : ""
  );
  const [observacoes, setObservacoes] = useState(
    isEditMode ? empresaParam.observacoes || "" : ""
  );

  // Endereco
  const [zipCode, setZipCode] = useState(
    isEditMode ? empresaParam.zip_code || "" : ""
  );
  const [street, setStreet] = useState(
    isEditMode ? empresaParam.street || "" : ""
  );
  const [number, setNumber] = useState(
    isEditMode ? empresaParam.number || "" : ""
  );
  const [district, setDistrict] = useState(
    isEditMode ? empresaParam.district || "" : ""
  );
  const [city, setCity] = useState(
    isEditMode ? empresaParam.city || "" : ""
  );
  const [state, setState] = useState(
    isEditMode ? empresaParam.state || "" : ""
  );
  const [complement, setComplement] = useState(
    isEditMode ? empresaParam.complement || "" : ""
  );

  // Toggles
  const [emitirNf, setEmitirNf] = useState(
    isEditMode ? !!empresaParam.emitir_nf : true
  );
  const [nfNaCriacao, setNfNaCriacao] = useState(
    isEditMode ? !!empresaParam.nf_na_criacao : false
  );
  const [issRetido, setIssRetido] = useState(
    isEditMode ? !!empresaParam.iss_retido : false
  );
  const [ativa, setAtiva] = useState(
    isEditMode ? empresaParam.ativo !== false : true
  );
  const [saving, setSaving] = useState(false);
  const [buscandoCep, setBuscandoCep] = useState(false);

  const alertMsg = (titulo: string, mensagem: string, onOk?: () => void) => {
    if (Platform.OS === "web") {
      window.alert(`${titulo}\n\n${mensagem}`);
      if (onOk) onOk();
    } else {
      Alert.alert(titulo, mensagem, [{ text: "OK", onPress: onOk }]);
    }
  };

  // Busca endereco pelo CEP (ViaCEP)
  const buscarCep = async () => {
    const cepDigits = zipCode.replace(/\D/g, "");
    if (cepDigits.length !== 8) {
      alertMsg("CEP invalido", "O CEP deve conter 8 digitos.");
      return;
    }

    setBuscandoCep(true);
    try {
      const resp = await fetch(`https://viacep.com.br/ws/${cepDigits}/json/`);
      const data = await resp.json();
      if (data.erro) {
        alertMsg("CEP nao encontrado", "Verifique o CEP informado.");
      } else {
        setStreet(data.logradouro || "");
        setDistrict(data.bairro || "");
        setCity(data.localidade || "");
        setState(data.uf || "");
        if (data.complemento) setComplement(data.complemento);
      }
    } catch {
      alertMsg("Erro", "Falha ao buscar CEP. Verifique sua conexao.");
    } finally {
      setBuscandoCep(false);
    }
  };

  const handleSalvar = async () => {
    const cnpjDigits = cnpj.replace(/\D/g, "");

    if (cnpjDigits.length !== 14) {
      alertMsg("CNPJ invalido", "O CNPJ deve conter 14 digitos.");
      return;
    }
    if (!razaoSocial.trim()) {
      alertMsg("Campo obrigatorio", "Informe a Razao Social.");
      return;
    }
    if (!email.trim()) {
      alertMsg("Campo obrigatorio", "Informe o E-mail.");
      return;
    }

    setSaving(true);

    if (isEditMode) {
      // Envia todos os campos para garantir que as alteracoes sejam salvas
      const dados: Record<string, any> = {
        razao_social: razaoSocial.trim(),
        email: email.trim(),
        descricao_boleto: descricaoBoleto.trim(),
        valor_fatura: valorFatura.trim(),
        dia_criacao_fatura: parseInt(diaCriacao) || 0,
        observacoes: observacoes.trim(),
        emitir_nf: emitirNf,
        nf_na_criacao: nfNaCriacao,
        iss_retido: issRetido,
        ativo: ativa,
        // Endereco
        zip_code: zipCode.replace(/\D/g, ""),
        street: street.trim(),
        number: number.trim(),
        district: district.trim(),
        city: city.trim(),
        state: state.trim().toUpperCase(),
        complement: complement.trim(),
      };

      const res = await editarEmpresa(cnpjDigits, dados);
      setSaving(false);

      if (res.data?.sucesso) {
        alertMsg(
          "Empresa atualizada",
          `${razaoSocial} foi atualizada com sucesso.`,
          () => {
            navigation.navigate("Empresas", { refresh: Date.now() });
          }
        );
      } else {
        alertMsg("Erro", res.error || "Falha ao atualizar empresa.");
      }
    } else {
      const dados: any = {
        cnpj: cnpjDigits,
        razao_social: razaoSocial.trim(),
        email: email.trim(),
        descricao_boleto: descricaoBoleto.trim(),
        valor_fatura: valorFatura.trim(),
        dia_criacao_fatura: parseInt(diaCriacao) || 0,
        observacoes: observacoes.trim(),
        emitir_nf: emitirNf,
        nf_na_criacao: nfNaCriacao,
        iss_retido: issRetido,
        // Endereco
        zip_code: zipCode.replace(/\D/g, ""),
        street: street.trim(),
        number: number.trim(),
        city: city.trim(),
        state: state.trim().toUpperCase(),
        district: district.trim(),
        complement: complement.trim(),
      };

      if (!ativa) {
        dados.ativo = false;
      }

      const res = await cadastrarEmpresa(dados);
      setSaving(false);

      if (res.data?.sucesso) {
        alertMsg(
          "Empresa cadastrada",
          `${razaoSocial} foi cadastrada com sucesso.\nCustomer Iugu: ${res.data.customer_id || "criado"}`,
          () => {
            navigation.navigate("Empresas", { refresh: Date.now() });
          }
        );
      } else {
        alertMsg("Erro", res.error || "Falha ao cadastrar empresa.");
      }
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        keyboardShouldPersistTaps="handled"
      >
        {/* Dados principais */}
        <Text style={styles.sectionTitle}>Dados da Empresa</Text>

        <Text style={styles.label}>CNPJ *</Text>
        <TextInput
          style={[styles.input, isEditMode && styles.inputDisabled]}
          placeholder="00.000.000/0000-00"
          value={cnpj}
          onChangeText={(v) => setCnpj(formatCnpjInput(v))}
          keyboardType="numeric"
          maxLength={18}
          editable={!isEditMode}
        />

        <Text style={styles.label}>Razao Social *</Text>
        <TextInput
          style={styles.input}
          placeholder="Nome da empresa"
          value={razaoSocial}
          onChangeText={setRazaoSocial}
          autoCapitalize="words"
        />

        <Text style={styles.label}>E-mail *</Text>
        <TextInput
          style={styles.input}
          placeholder="contato@empresa.com"
          value={email}
          onChangeText={setEmail}
          keyboardType="email-address"
          autoCapitalize="none"
        />

        {/* Endereco */}
        <Text style={[styles.sectionTitle, { marginTop: 20 }]}>Endereco</Text>

        <Text style={styles.label}>CEP</Text>
        <View style={styles.cepRow}>
          <TextInput
            style={[styles.input, { flex: 1 }]}
            placeholder="00000-000"
            value={zipCode}
            onChangeText={(v) => setZipCode(formatCepInput(v))}
            keyboardType="numeric"
            maxLength={9}
          />
          <TouchableOpacity
            style={styles.cepBtn}
            onPress={buscarCep}
            disabled={buscandoCep}
          >
            {buscandoCep ? (
              <ActivityIndicator size="small" color="#fff" />
            ) : (
              <Ionicons name="search" size={18} color="#fff" />
            )}
          </TouchableOpacity>
        </View>

        <Text style={styles.label}>Rua / Logradouro</Text>
        <TextInput
          style={styles.input}
          placeholder="Rua, Avenida..."
          value={street}
          onChangeText={setStreet}
        />

        <View style={styles.row}>
          <View style={{ flex: 1 }}>
            <Text style={styles.label}>Numero</Text>
            <TextInput
              style={styles.input}
              placeholder="123"
              value={number}
              onChangeText={setNumber}
            />
          </View>
          <View style={{ flex: 2, marginLeft: 12 }}>
            <Text style={styles.label}>Complemento</Text>
            <TextInput
              style={styles.input}
              placeholder="Sala, Andar..."
              value={complement}
              onChangeText={setComplement}
            />
          </View>
        </View>

        <Text style={styles.label}>Bairro</Text>
        <TextInput
          style={styles.input}
          placeholder="Bairro"
          value={district}
          onChangeText={setDistrict}
        />

        <View style={styles.row}>
          <View style={{ flex: 2 }}>
            <Text style={styles.label}>Cidade</Text>
            <TextInput
              style={styles.input}
              placeholder="Cidade"
              value={city}
              onChangeText={setCity}
            />
          </View>
          <View style={{ flex: 1, marginLeft: 12 }}>
            <Text style={styles.label}>UF</Text>
            <TextInput
              style={styles.input}
              placeholder="DF"
              value={state}
              onChangeText={(v) => setState(v.toUpperCase())}
              maxLength={2}
              autoCapitalize="characters"
            />
          </View>
        </View>

        {/* Cobranca */}
        <Text style={[styles.sectionTitle, { marginTop: 20 }]}>Cobranca</Text>

        <Text style={styles.label}>Descricao no Boleto</Text>
        <TextInput
          style={styles.input}
          placeholder="Texto que aparece no boleto"
          value={descricaoBoleto}
          onChangeText={setDescricaoBoleto}
        />

        <View style={styles.row}>
          <View style={styles.halfField}>
            <Text style={styles.label}>Valor Fatura (R$)</Text>
            <TextInput
              style={styles.input}
              placeholder="1850,00"
              value={valorFatura}
              onChangeText={setValorFatura}
              keyboardType="decimal-pad"
            />
          </View>
          <View style={styles.halfField}>
            <Text style={styles.label}>Dia Cobranca (1-31)</Text>
            <TextInput
              style={styles.input}
              placeholder="0 = sem recorrencia"
              value={diaCriacao}
              onChangeText={setDiaCriacao}
              keyboardType="number-pad"
              maxLength={2}
            />
          </View>
        </View>

        <Text style={styles.label}>Observacoes</Text>
        <TextInput
          style={[styles.input, styles.multiline]}
          placeholder="Observacoes internas"
          value={observacoes}
          onChangeText={setObservacoes}
          multiline
          numberOfLines={3}
        />

        {/* Configuracoes */}
        <Text style={[styles.sectionTitle, { marginTop: 20 }]}>
          Configuracoes
        </Text>

        <View style={styles.toggleRow}>
          <Text style={styles.toggleLabel}>Emitir NF-e</Text>
          <Switch
            value={emitirNf}
            onValueChange={setEmitirNf}
            trackColor={{ false: "#d1d5db", true: "#93c5fd" }}
            thumbColor={emitirNf ? "#1a56db" : "#f4f3f4"}
          />
        </View>

        <View style={styles.toggleRow}>
          <Text style={styles.toggleLabel}>NF na Criacao</Text>
          <Switch
            value={nfNaCriacao}
            onValueChange={setNfNaCriacao}
            trackColor={{ false: "#d1d5db", true: "#93c5fd" }}
            thumbColor={nfNaCriacao ? "#1a56db" : "#f4f3f4"}
          />
        </View>

        <View style={styles.toggleRow}>
          <Text style={styles.toggleLabel}>ISS retido na fonte</Text>
          <Switch
            value={issRetido}
            onValueChange={setIssRetido}
            trackColor={{ false: "#d1d5db", true: "#93c5fd" }}
            thumbColor={issRetido ? "#1a56db" : "#f4f3f4"}
          />
        </View>

        <View style={styles.toggleRow}>
          <Text style={styles.toggleLabel}>Ativa</Text>
          <Switch
            value={ativa}
            onValueChange={setAtiva}
            trackColor={{ false: "#d1d5db", true: "#93c5fd" }}
            thumbColor={ativa ? "#1a56db" : "#f4f3f4"}
          />
        </View>

        {/* Botao salvar */}
        <TouchableOpacity
          style={[styles.saveBtn, saving && styles.saveBtnDisabled]}
          onPress={handleSalvar}
          disabled={saving}
        >
          {saving ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <>
              <Ionicons name="checkmark-circle" size={20} color="#fff" />
              <Text style={styles.saveBtnText}>
                {isEditMode ? "Salvar Alteracoes" : "Salvar"}
              </Text>
            </>
          )}
        </TouchableOpacity>

        <View style={{ height: 40 }} />
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#f3f4f6" },
  scrollContent: { padding: 16 },
  sectionTitle: {
    fontSize: 16,
    fontWeight: "700",
    color: "#1a56db",
    marginBottom: 12,
    marginTop: 4,
  },
  label: {
    fontSize: 13,
    fontWeight: "600",
    color: "#374151",
    marginBottom: 4,
    marginTop: 8,
  },
  input: {
    backgroundColor: "#fff",
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 10,
    fontSize: 14,
    color: "#111827",
    borderWidth: 1,
    borderColor: "#e5e7eb",
  },
  inputDisabled: {
    backgroundColor: "#f3f4f6",
    color: "#9ca3af",
  },
  multiline: {
    minHeight: 60,
    textAlignVertical: "top",
  },
  row: {
    flexDirection: "row",
    gap: 12,
  },
  halfField: {
    flex: 1,
  },
  cepRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  cepBtn: {
    backgroundColor: "#1a56db",
    borderRadius: 10,
    padding: 12,
    justifyContent: "center",
    alignItems: "center",
  },
  toggleRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: "#fff",
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    marginTop: 8,
    borderWidth: 1,
    borderColor: "#e5e7eb",
  },
  toggleLabel: {
    fontSize: 14,
    color: "#374151",
  },
  saveBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    backgroundColor: "#1a56db",
    borderRadius: 10,
    paddingVertical: 16,
    marginTop: 24,
  },
  saveBtnDisabled: {
    opacity: 0.6,
  },
  saveBtnText: {
    color: "#fff",
    fontSize: 16,
    fontWeight: "700",
  },
});

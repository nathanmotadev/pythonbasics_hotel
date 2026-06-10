

class Cadastro:
    def __init__(self, nome, idade, email, quantidade_de_cadastros):
        self.nome = nome
        self.idade = idade
        self.email = email
        

    def __str__(self):
        return f"\nNome: {self.nome}\n idade: {self.idade}\n email: {self.email}"
    

    
    def criar_cadastro():    
        nome = input("diigite seu nome compelto:")
        idade = int(input("digite sua idade: "))
        email = input("digite seu email: ")    
        return Cadastro(nome, idade, email)

    def editar_cadastro(self, nome=None, idade= None, email= None):
        if nome:
            self.nome = nome
        if idade:
            self.idade = idade
        if email:
            self.email = email
class Equipe:
    def __init__(self, nome, idade, cargo, salario, quantidadeFuncionarios):
        self.nome = nome 
        self.idade = idade 
        self.cargo = cargo
        self.salario = salario 
        self.quantidadeFuncionarios = quantidadeFuncionarios

    def AdiconarFuncionario(self, quantidade = 1):
        self.quantidadeFuncionarios += quantidade
        



class menu_gastronimico: 
    def __init__(self, opcoes):
        self.opcoes = opcoes

opcoes = ('''
1. ☕️ Café da manhã
2. 🍴 Almoço
3. 🍽️ Jantar
4. 🍔 Lanches
''')
        
        
def exibir_menu():
    print(opcoes.opcoes)

nathan = Cadastro('nathan mota de barros', 21, 'nathanmota21@hotmail.com', 2)
Lea = Equipe('lea wallace',35,'Chefe',3000, 0)
print (f" Nome:{Lea.nome}\n Cargo:{Lea.cargo}\n Salário:{Lea.salario}")
print ( Lea.quantidadeFuncionarios )
Lea.AdiconarFuncionario(3)
print (opcoes)
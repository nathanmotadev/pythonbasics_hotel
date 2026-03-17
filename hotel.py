
nome = (input("digite seu nome: "))
menu = f'''
++++++++++++ MENU ++++++++++++
1. Café da manhã
2. Almoço
3. Jantar
4. Lanches
++++++++++++++++++++++++++++++
'''
mensagem = f'''
------------ bem vindo {nome}!!! ------------
A pousada LaMa agradece a sua preferência, esperamos que tenha uma ótima estadia.
  
Abaixo estão os serviços que oferecemos, caso queira solicitar algum deles,
    basta digitar o número correspondente.

++++++++++++ MENU ++++++++++++
1. Café da manhã
2. Almoço
3. Jantar
4. Lanches
++++++++++++++++++++++++++++++
'''


print (mensagem)

if True:
    pedido = int(input("digite o o número do serviço que deseja: "))
    if pedido == 1:
                print ("seu café da manhã já está a caminho")
    elif pedido == 2:
        print ("seu almoço já está a caminho")
    elif pedido == 3:
        print ("seu jantar já está a caminho")
    elif pedido == 4:
        print ("seu lanche já está a caminho")
 
print (("podemos te ajudar em mais alguma coisa, sr.(a) {} ?").format(nome))
pedido2 = input ("digite sim ou não: ")
while pedido2 == "sim":
    print (menu)
    pedido = int(input("digite o o número do serviço que deseja: "))
    if pedido == 1:
                print ("seu café da manhã já está a caminho")
    elif pedido == 2:
        print ("seu almoço já está a caminho")
    elif pedido == 3:
        print ("seu jantar já está a caminho")
    elif pedido == 4:
        print ("seu lanche já está a caminho")
    print (("podemos te ajudar em mais alguma coisa, sr.(a) {} ?").format(nome))
    pedido2 = input ("digite sim ou não: ")
    if pedido2 == "não":
        break
if pedido2 == "não":
    print ("obrigado por escolher a pousada LaMa, esperamos que tenha uma ótima estadia")
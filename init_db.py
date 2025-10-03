from db import Base, engine
import models  # noqa: F401


def main():
    Base.metadata.create_all(bind=engine)
    print("✅ Tablas creadas/actualizadas.")


# if __name__ == "__main__":
#     main()
